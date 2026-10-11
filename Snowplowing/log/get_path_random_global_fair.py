import argparse
import datetime
import heapq
import json
import math
import os
import random
import xml.etree.ElementTree as ET
from collections import defaultdict

import networkx as nx
from pymongo import MongoClient


CONNECTION_URL = "mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017"
OD_DB_NAME = "tsingroc"
OD_COLL_NAME = "person_test_20250724_1015cc"
OD_OUTPUT_NAME = "current_od_file.json"
DEFAULT_MAX_ITER = 1000


def export_mongodb_to_json(connection_url, database_name, collection_name, output_filename=None):
    client = None
    try:
        print("Connecting to MongoDB...")
        client = MongoClient(connection_url)
        client.admin.command("ping")

        db = client[database_name]
        collection = db[collection_name]
        doc_count = collection.count_documents({})
        print(f"Collection {collection_name} has {doc_count} records")

        if doc_count == 0:
            return None

        documents = list(collection.find())

        if output_filename is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"{database_name}.{collection_name}_{timestamp}.json"
        if not output_filename.endswith(".json"):
            output_filename += ".json"

        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(documents, f, indent=2, ensure_ascii=False, default=str)

        print(f"Exported records to {output_filename}")
        return documents
    except Exception as exc:
        print(f"Failed to export MongoDB data: {exc}")
        return None
    finally:
        if client:
            client.close()


def euclid_dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def build_traffic_flow(od_docs):
    traffic_flow = defaultdict(int)
    if not od_docs:
        return traffic_flow

    for ind_od in od_docs:
        try:
            road_ids = ind_od["data"]["schedules"][0]["trips"][0]["routes"][0]["driving"]["road_ids"]
        except (KeyError, IndexError, TypeError):
            continue
        for road_id in road_ids:
            traffic_flow[road_id] += 1
    return traffic_flow


def get_random_global_fair_snowplow_path(
    od_docs,
    net_path="/home/nkk/work/src/python/Snowplowing/Shaped.net.xml",
    clean_speed=3.0,
    pass_speed=10.0,
    route_save_path="/home/nkk/work/src/python/Snowplowing/plowing_path/cleaned_roads_by_interval_random_global_fair.json",
    record_window=1800,
    max_iter=DEFAULT_MAX_ITER,
    seed=42,
):
    rng = random.Random(seed)
    start_edges = {
        "0": "200000550",
        "1": "200000543",
        "2": "200000560",
        "3": "200000558",
    }

    tree = ET.parse(net_path)
    root = tree.getroot()
    traffic_flow = build_traffic_flow(od_docs)

    all_nodes = set()
    G = nx.DiGraph()
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if edge_id.startswith(":"):
            continue
        from_node = edge.get("from")
        to_node = edge.get("to")
        if from_node:
            all_nodes.add(from_node)
        if to_node:
            all_nodes.add(to_node)

    for node in all_nodes:
        G.add_node(node)

    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if edge_id.startswith(":"):
            continue
        from_node = edge.get("from")
        to_node = edge.get("to")
        if not from_node or not to_node:
            continue

        lanes = edge.findall("lane")
        if not lanes:
            continue

        edge_length = float(lanes[0].get("length"))
        lane_count = len(lanes)
        G.add_edge(
            from_node,
            to_node,
            id=edge_id,
            length=edge_length,
            lane_count=lane_count,
            total_length=edge_length * lane_count,
        )

    node_positions = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if edge_id.startswith(":"):
            continue
        lanes = edge.findall("lane")
        if not lanes:
            continue
        shape_attr = lanes[0].get("shape")
        if not shape_attr:
            continue
        coords = [tuple(map(float, p.split(","))) for p in shape_attr.split()]
        if len(coords) < 2:
            continue

        from_coord = coords[0]
        to_coord = coords[-1]
        from_node = edge.get("from")
        to_node = edge.get("to")
        if from_node in G.nodes and from_node not in node_positions:
            node_positions[from_node] = from_coord
        if to_node in G.nodes and to_node not in node_positions:
            node_positions[to_node] = to_coord

    edge_data_dict = {}
    for u, v, d in G.edges(data=True):
        eid = d["id"]
        edge_data_dict[eid] = {
            "from": u,
            "to": v,
            "single_length": float(d["length"]),
            "lane_count": d["lane_count"],
            "length": float(d["length"]) * d["lane_count"],
            "flow": traffic_flow.get(eid, 0),
        }

    car_states = {}
    for car_id, start_edge in start_edges.items():
        if start_edge not in edge_data_dict:
            raise KeyError(f"Start edge not found in network: {start_edge}")
        car_states[car_id] = {
            "current_node": edge_data_dict[start_edge]["from"],
            "cleaned_edges": set(),
            "cleaning_paths": [],
            "cleaned_length": 0.0,
        }

    cleaned_edges = set()
    all_edge_ids = set(edge_data_dict.keys())
    stagnation_rounds = 0

    for _ in range(max_iter):
        progress = False
        for _, state in car_states.items():
            current_node = state["current_node"]

            uncleaned_successors = []
            for _, nxt, d in G.out_edges(current_node, data=True):
                if d["id"] not in cleaned_edges:
                    uncleaned_successors.append((current_node, nxt, d))

            if uncleaned_successors:
                this_start_node, next_node, chosen_edge = rng.choice(uncleaned_successors)
                eid = chosen_edge["id"]

                cleaned_edges.add(eid)
                state["cleaned_edges"].add(eid)
                state["cleaned_length"] += edge_data_dict[eid]["length"]
                state["cleaning_paths"].extend([eid] * edge_data_dict[eid]["lane_count"])

                if chosen_edge["lane_count"] % 2 == 0:
                    state["current_node"] = this_start_node
                else:
                    state["current_node"] = next_node
                progress = True
                continue

            remaining = [eid for eid in all_edge_ids if eid not in cleaned_edges]
            if not remaining:
                continue

            candidate_nodes = list(
                {edge_data_dict[eid]["from"] for eid in remaining if edge_data_dict[eid]["from"] != current_node}
            )
            if not candidate_nodes:
                continue

            def node_min_dist(node_id):
                if current_node in node_positions and node_id in node_positions:
                    return euclid_dist(node_positions[current_node], node_positions[node_id])
                return float("inf")

            nearest_nodes = heapq.nsmallest(min(10, len(candidate_nodes)), candidate_nodes, key=node_min_dist)
            rng.shuffle(nearest_nodes)
            path_found = False

            for nid in nearest_nodes:
                try:
                    path_nodes = nx.shortest_path(G, source=current_node, target=nid, weight="length")
                except Exception:
                    continue

                path_found = True
                found_uncleaned_on_path = False

                for j in range(len(path_nodes) - 1):
                    u, v = path_nodes[j], path_nodes[j + 1]
                    eid = G[u][v]["id"]

                    if eid in cleaned_edges:
                        state["cleaning_paths"].append(eid)
                        state["current_node"] = v
                        continue

                    cleaned_edges.add(eid)
                    state["cleaned_edges"].add(eid)
                    state["cleaned_length"] += edge_data_dict[eid]["length"]
                    state["cleaning_paths"].extend([eid] * edge_data_dict[eid]["lane_count"])

                    if edge_data_dict[eid]["lane_count"] % 2 == 0:
                        state["current_node"] = u
                    else:
                        state["current_node"] = v

                    found_uncleaned_on_path = True
                    progress = True
                    break

                if not found_uncleaned_on_path:
                    state["current_node"] = nid
                break

            if not path_found:
                state["cleaning_paths"].append("penalty")

        if cleaned_edges == all_edge_ids:
            break

        if not progress:
            stagnation_rounds += 1
        else:
            stagnation_rounds = 0

        if stagnation_rounds >= 20:
            print("Warning: no progress for many rounds, stopping early.")
            break

    car_times = {}
    car_total_times = {}
    for car_id, state in car_states.items():
        t = 0.0
        cleaned_set = set()
        seq = []
        for token in state["cleaning_paths"]:
            if token == "penalty":
                t += 3000.0 / pass_speed
                seq.append((token, t))
                continue

            length = edge_data_dict[token]["single_length"]
            if token not in cleaned_set:
                t += length / clean_speed
                cleaned_set.add(token)
            else:
                t += length / pass_speed
            seq.append((token, t))

        car_times[car_id] = seq
        car_total_times[car_id] = t

    cleaned_roads_by_interval = {}
    max_intervals = 10
    for car_id, seq in car_times.items():
        for interval_num in range(1, max_intervals + 1):
            time_threshold = interval_num * record_window
            interval_key = f"interval_{interval_num}"
            if interval_key not in cleaned_roads_by_interval:
                cleaned_roads_by_interval[interval_key] = {}

            cleaned_roads = []
            for eid, cumulative_time in seq:
                if eid != "penalty" and cumulative_time <= time_threshold:
                    cleaned_roads.append(eid)

            cleaned_roads_by_interval[interval_key][str(car_id)] = list(dict.fromkeys(cleaned_roads))

            if time_threshold >= car_total_times[car_id]:
                break

    os.makedirs(os.path.dirname(route_save_path), exist_ok=True)
    with open(route_save_path, "w", encoding="utf-8") as f:
        json.dump(cleaned_roads_by_interval, f, indent=2, ensure_ascii=False)

    print(f"Saved fair random global path to: {route_save_path}")


def main():
    parser = argparse.ArgumentParser(description="Global random snowplow path planner comparable to global greedy")
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
        default="/home/nkk/work/src/python/Snowplowing/plowing_path/cleaned_roads_by_interval_random_global_fair.json",
    )
    parser.add_argument("--max_iter", type=int, default=DEFAULT_MAX_ITER)
    parser.add_argument("--record_window", type=int, default=1800)
    parser.add_argument("--seed", type=int, default=42)
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

    get_random_global_fair_snowplow_path(
        od_docs=od_docs,
        net_path=args.net_file,
        clean_speed=args.clean_speed,
        pass_speed=args.pass_speed,
        route_save_path=args.route_save_path,
        record_window=args.record_window,
        max_iter=args.max_iter,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()