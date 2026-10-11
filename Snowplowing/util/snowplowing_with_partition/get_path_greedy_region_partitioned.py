import argparse
import heapq
import json
import os
import xml.etree.ElementTree as ET
from collections import defaultdict

import networkx as nx

from get_path_greedy import (
    CONNECTION_URL,
    DEFAULT_MAX_ITER,
    DEFAULT_MAX_NO_PROGRESS,
    OD_COLL_NAME,
    OD_DB_NAME,
    OD_OUTPUT_NAME,
    START_EDGES,
    build_traffic_flow,
    euclid_dist,
    export_mongodb_to_json,
    load_network,
    pick_successor_greedy,
)


def parse_shape(shape_str):
    if not shape_str:
        return []
    points = []
    for point_str in shape_str.split():
        x, y = map(float, point_str.split(","))
        points.append((x, y))
    return points


def get_edge_bounds(edge_elem):
    shape_str = edge_elem.get("shape", "")
    all_points = []
    if shape_str:
        all_points.extend(parse_shape(shape_str))

    if not all_points:
        for lane in edge_elem.findall("lane"):
            lane_shape = lane.get("shape", "")
            if lane_shape:
                all_points.extend(parse_shape(lane_shape))
                break

    if not all_points:
        return None

    xs = [point[0] for point in all_points]
    ys = [point[1] for point in all_points]
    return min(xs), min(ys), max(xs), max(ys)


def classify_region(bounds, x_mid, y_mid):
    min_x, min_y, max_x, max_y = bounds
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2
    if center_x < x_mid and center_y < y_mid:
        return 0
    if center_x >= x_mid and center_y < y_mid:
        return 1
    if center_x < x_mid and center_y >= y_mid:
        return 2
    return 3


def load_edge_regions(net_path):
    tree = ET.parse(net_path)
    root = tree.getroot()

    edge_regions = {}
    missing_region_edges = []
    edge_bounds = {}
    edge_centers = []

    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if not edge_id or edge_id.startswith(":") or edge.get("function") == "internal":
            continue

        bounds = get_edge_bounds(edge)
        if bounds is None:
            continue

        edge_bounds[edge_id] = bounds
        edge_centers.append(((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2))

        region_attr = edge.get("region")
        if region_attr is None:
            missing_region_edges.append(edge_id)
        else:
            edge_regions[edge_id] = int(region_attr)

    if not edge_bounds:
        raise RuntimeError("No valid edge geometry found in network file")

    if not missing_region_edges:
        return edge_regions

    x_coords = sorted(center[0] for center in edge_centers)
    y_coords = sorted(center[1] for center in edge_centers)
    x_mid = x_coords[len(x_coords) // 2]
    y_mid = y_coords[len(y_coords) // 2]

    for edge_id, bounds in edge_bounds.items():
        edge_regions[edge_id] = classify_region(bounds, x_mid, y_mid)

    return edge_regions


def simulate_paths_by_region(
    G,
    edge_data_dict,
    edge_regions,
    node_positions,
    traffic_flow,
    clean_speed,
    pass_speed,
    record_window,
    max_iter,
    max_no_progress,
    penalty_distance,
):
    region_edges = defaultdict(set)
    for edge_id in edge_data_dict:
        region_id = edge_regions.get(edge_id)
        if region_id is not None:
            region_edges[region_id].add(edge_id)

    car_states = {}
    for car_id, start_edge in START_EDGES.items():
        if start_edge not in edge_data_dict:
            raise KeyError(f"Start edge not found in network: {start_edge}")

        target_region = int(car_id)
        start_region = edge_regions.get(start_edge)
        if start_region is not None and start_region != target_region:
            raise ValueError(
                f"Car {car_id} start edge {start_edge} is in region {start_region}, expected region {target_region}"
            )

        car_states[car_id] = {
            "current_node": edge_data_dict[start_edge]["from"],
            "cleaned_edges": set(),
            "cleaning_paths": [],
            "cleaned_length": 0.0,
            "target_region": target_region,
            "responsible_edges": set(region_edges.get(target_region, set())),
        }

    cleaned_edges = set()
    stagnation_rounds = 0

    for _ in range(max_iter):
        progress = False
        for car_id, state in car_states.items():
            current_node = state["current_node"]
            responsible_edges = state["responsible_edges"]

            uncleaned_successors = []
            for _, nxt, edge_info in G.out_edges(current_node, data=True):
                edge_id = edge_info["id"]
                if edge_id in responsible_edges and edge_id not in cleaned_edges:
                    uncleaned_successors.append((current_node, nxt, edge_info))

            if uncleaned_successors:
                this_start_node, next_node, chosen_edge = pick_successor_greedy(uncleaned_successors, traffic_flow)
                edge_id = chosen_edge["id"]

                cleaned_edges.add(edge_id)
                state["cleaned_edges"].add(edge_id)
                state["cleaned_length"] += edge_data_dict[edge_id]["length"]
                state["cleaning_paths"].extend([(edge_id, "clean")] * edge_data_dict[edge_id]["lane_count"])

                if chosen_edge["lane_count"] % 2 == 0:
                    state["current_node"] = this_start_node
                else:
                    state["current_node"] = next_node

                progress = True
                continue

            remaining = [edge_id for edge_id in responsible_edges if edge_id not in cleaned_edges]
            if not remaining:
                continue

            candidate_nodes = list(
                {
                    edge_data_dict[edge_id]["from"]
                    for edge_id in remaining
                    if edge_data_dict[edge_id]["from"] != current_node
                }
            )
            if not candidate_nodes:
                continue

            def node_min_dist(node_id):
                if current_node in node_positions and node_id in node_positions:
                    return euclid_dist(node_positions[current_node], node_positions[node_id])
                return float("inf")

            nearest_nodes = heapq.nsmallest(min(10, len(candidate_nodes)), candidate_nodes, key=node_min_dist)
            path_found = False

            for node_id in nearest_nodes:
                try:
                    path_nodes = nx.shortest_path(G, source=current_node, target=node_id, weight="length")
                except Exception:
                    continue

                path_found = True
                found_uncleaned_on_path = False

                for index in range(len(path_nodes) - 1):
                    u, v = path_nodes[index], path_nodes[index + 1]
                    edge_id = G[u][v]["id"]

                    if edge_id not in responsible_edges:
                        state["cleaning_paths"].append((edge_id, "pass"))
                        state["current_node"] = v
                        continue

                    if edge_id in cleaned_edges:
                        state["cleaning_paths"].append((edge_id, "pass"))
                        state["current_node"] = v
                        continue

                    cleaned_edges.add(edge_id)
                    state["cleaned_edges"].add(edge_id)
                    state["cleaned_length"] += edge_data_dict[edge_id]["length"]
                    state["cleaning_paths"].extend([(edge_id, "clean")] * edge_data_dict[edge_id]["lane_count"])

                    if edge_data_dict[edge_id]["lane_count"] % 2 == 0:
                        state["current_node"] = u
                    else:
                        state["current_node"] = v

                    found_uncleaned_on_path = True
                    progress = True
                    break

                if not found_uncleaned_on_path:
                    state["current_node"] = node_id
                break

            if not path_found:
                state["cleaning_paths"].append(("penalty", "penalty"))

        all_responsible_edges = set()
        for state in car_states.values():
            all_responsible_edges.update(state["responsible_edges"])
        if cleaned_edges >= all_responsible_edges:
            break

        if not progress:
            stagnation_rounds += 1
        else:
            stagnation_rounds = 0

        if stagnation_rounds >= max_no_progress:
            print("Warning: no progress for many rounds, stopping early.")
            break

    car_times = {}
    car_total_times = {}
    for car_id, state in car_states.items():
        elapsed = 0.0
        seq = []
        for token, mode in state["cleaning_paths"]:
            if mode == "penalty":
                elapsed += penalty_distance / pass_speed
                seq.append((token, mode, elapsed))
                continue

            length = edge_data_dict[token]["single_length"]
            if mode == "clean":
                elapsed += length / clean_speed
            else:
                elapsed += length / pass_speed
            seq.append((token, mode, elapsed))

        car_times[car_id] = seq
        car_total_times[car_id] = elapsed

    cleaned_roads_by_interval = {}
    max_intervals = 10
    for car_id, seq in car_times.items():
        for interval_num in range(1, max_intervals + 1):
            time_threshold = interval_num * record_window
            interval_key = f"interval_{interval_num}"
            if interval_key not in cleaned_roads_by_interval:
                cleaned_roads_by_interval[interval_key] = {}

            cleaned_roads = []
            for edge_id, mode, cumulative_time in seq:
                if mode == "clean" and cumulative_time <= time_threshold:
                    cleaned_roads.append(edge_id)

            cleaned_roads_by_interval[interval_key][str(car_id)] = list(dict.fromkeys(cleaned_roads))

            if time_threshold >= car_total_times[car_id]:
                break

    return cleaned_roads_by_interval


def get_greedy_region_partitioned_fair_constrained_path(
    od_docs,
    net_path="/home/nkk/work/src/python/Snowplowing/Shaped.net.xml",
    clean_speed=3.0,
    pass_speed=10.0,
    route_save_path="/home/nkk/work/src/python/Snowplowing/plowing_path/greedy_region_partitioned.json",
    record_window=1800,
    max_iter=DEFAULT_MAX_ITER,
    max_no_progress=DEFAULT_MAX_NO_PROGRESS,
    penalty_distance=3000.0,
):
    traffic_flow = build_traffic_flow(od_docs)
    G, edge_data_dict, node_positions = load_network(net_path, traffic_flow)
    edge_regions = load_edge_regions(net_path)

    result = simulate_paths_by_region(
        G=G,
        edge_data_dict=edge_data_dict,
        edge_regions=edge_regions,
        node_positions=node_positions,
        traffic_flow=traffic_flow,
        clean_speed=clean_speed,
        pass_speed=pass_speed,
        record_window=record_window,
        max_iter=max_iter,
        max_no_progress=max_no_progress,
        penalty_distance=penalty_distance,
    )

    os.makedirs(os.path.dirname(route_save_path), exist_ok=True)
    with open(route_save_path, "w", encoding="utf-8") as file_obj:
        json.dump(result, file_obj, indent=2, ensure_ascii=False)

    print(f"Saved four-region fair constrained greedy path to: {route_save_path}")


def main():
    parser = argparse.ArgumentParser(description="Four-region fair and constrained greedy snowplow path planner")
    parser.add_argument("--net_file", type=str, default="/home/nkk/work/src/python/Snowplowing/Shaped.net.xml")
    parser.add_argument("--od_mongo_uri", type=str, default=CONNECTION_URL)
    parser.add_argument("--od_db", type=str, default=OD_DB_NAME)
    parser.add_argument("--od_coll", type=str, default=OD_COLL_NAME)
    parser.add_argument("--od_output", type=str, default=OD_OUTPUT_NAME)
    parser.add_argument("--clean_speed", type=float, default=3.0)
    parser.add_argument("--pass_speed", type=float, default=10.0)
    parser.add_argument(
        "--route_save_path",
        type=str,
        default="/home/nkk/work/src/python/Snowplowing/plowing_path/greedy_region_partitioned.json",
    )
    parser.add_argument("--max_iter", type=int, default=DEFAULT_MAX_ITER)
    parser.add_argument("--max_no_progress", type=int, default=DEFAULT_MAX_NO_PROGRESS)
    parser.add_argument("--record_window", type=int, default=1800)
    parser.add_argument("--penalty_distance", type=float, default=3000.0)
    args = parser.parse_args()

    od_docs = export_mongodb_to_json(
        connection_url=args.od_mongo_uri,
        database_name=args.od_db,
        collection_name=args.od_coll,
        output_filename=args.od_output,
    )
    if not od_docs:
        print("No OD data found, exiting.")
        return

    get_greedy_region_partitioned_fair_constrained_path(
        od_docs=od_docs,
        net_path=args.net_file,
        clean_speed=args.clean_speed,
        pass_speed=args.pass_speed,
        route_save_path=args.route_save_path,
        record_window=args.record_window,
        max_iter=args.max_iter,
        max_no_progress=args.max_no_progress,
        penalty_distance=args.penalty_distance,
    )


if __name__ == "__main__":
    main()