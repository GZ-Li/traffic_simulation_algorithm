#!/usr/bin/env python3
"""
Public Release Version of the Mapping Converter Script with Automatic Merging.

This script converts map and agent files (in protocol buffer format) into SUMO-compatible XML files
(edges, nodes, and connections) and automatically merges them into a network file using netconvert.
It also detects whether SUMO is installed on the system.

Author: djl (Enhanced for public release)
Date: 2025-04-03
"""

import sys
import shutil
import subprocess
import xml.etree.ElementTree as ET
import numpy as np
import pyproj
from pycityproto.city.map.v2.light_pb2 import LightState
from pycityproto.city.map.v2.map_pb2 import Lane
from mosstool.type import LaneType, Map, Persons


def complete_projection(proj_str: str, min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    if not proj_str or not proj_str.strip():
        center_lat = (min_lat + max_lat) / 2.0
        center_lon = (min_lon + max_lon) / 2.0
        return (f"+proj=tmerc +lat_0={center_lat:.6f} +lon_0={center_lon:.6f} "
                "+k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs")
    tokens = proj_str.strip().split()
    token_keys = {tok.split("=")[0] for tok in tokens}
    defaults = {
        "+k": "1",
        "+x_0": "0",
        "+y_0": "0",
        "+ellps": "WGS84",
        "+units": "m",
        "+no_defs": None,
    }
    for key, val in defaults.items():
        if key not in token_keys:
            tokens.append(f"{key}={val}" if val else key)
    return " ".join(tokens)


def convert(
    map_file: str,
    agent_file: str,
    output_edge: str,
    output_node: str,
    output_connection: str,
) -> None:
    """
    Converts map and agent data into SUMO-compatible XML files.
    Only adds a 'shape' attribute to each <edge>, leaving other logic intact.
    """
    # Read map
    with open(map_file, "rb") as f:
        m = Map(); m.ParseFromString(f.read())

    # Build lane dict
    lanes_dict = {l.id: l for l in m.lanes}

    # Coordinate offsets
    x_offset = -m.header.west
    y_offset = -m.header.south
    projector = pyproj.Proj(m.header.projection)
    min_lon, min_lat = projector(m.header.west, m.header.south, inverse=True)
    max_lon, max_lat = projector(m.header.east, m.header.north, inverse=True)

    # XML roots
    edges_root = ET.Element("edges", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "version": "1.20",
        "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/edges_file.xsd"
    })
    nodes_root = ET.Element("nodes", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "version": "1.20",
        "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/nodes_file.xsd"
    })
    ET.SubElement(nodes_root, "location", {
        "netOffset": f"{x_offset:.2f},{y_offset:.2f}",
        "convBoundary": f"0.00,0.00,{m.header.east+x_offset:.2f},{m.header.north+y_offset:.2f}",
        "origBoundary": f"{min_lon:.6f},{min_lat:.6f},{max_lon:.6f},{max_lat:.6f}",
        "projParameter": complete_projection(m.header.projection, min_lon, min_lat, max_lon, max_lat)
    })
    connections_root = ET.Element("connections", {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "version": "1.20",
        "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/connections_file.xsd"
    })

    lane_index_map = {}
    valid_edge_ids = set()
    conn_to_road_junc_ids = set()

    for road in m.roads:
        # driving lanes
        road_lanes = [lanes_dict[lid] for lid in road.lane_ids if lid in lanes_dict]
        driving_lanes = [ln for ln in road_lanes if ln.type == LaneType.LANE_TYPE_DRIVING]
        # predecessors/successors
        pre_ids, suc_ids = set(), set()
        for ln in driving_lanes:
            for pre in ln.predecessors:
                if pre.id in lanes_dict:
                    pre_ids.add(lanes_dict[pre.id].parent_id)
            for suc in ln.successors:
                if suc.id in lanes_dict:
                    suc_ids.add(lanes_dict[suc.id].parent_id)
        if len(pre_ids)>1 or len(suc_ids)>1:
            continue
        pre_junc = next(iter(pre_ids), -1)
        suc_junc = next(iter(suc_ids), -1)
        if pre_junc<0 or suc_junc<0 or pre_junc==suc_junc:
            continue
        conn_to_road_junc_ids.update({pre_junc, suc_junc})
        valid_edge_ids.add(road.id)
        # lane index map
        lane_index_map[road.id] = {ln.id: idx for idx, ln in enumerate(reversed(driving_lanes))}
        # add edge with shape
        # use first driving lane for shape
        shape_coords = []
        if driving_lanes:
            shape_coords = [f"{nd.x+x_offset:.2f},{nd.y+y_offset:.2f}" for nd in driving_lanes[0].center_line.nodes]
        ET.SubElement(
            edges_root,
            "edge",
            {
                "id": str(road.id),
                "from": str(pre_junc),
                "to": str(suc_junc),
                "priority": "1",
                "numLanes": str(len(driving_lanes)),
                "speed": str(driving_lanes[0].max_speed) if driving_lanes else "0",
                "shape": " ".join(shape_coords)
            }
        )

    for junc in m.junctions:
        if junc.id not in conn_to_road_junc_ids:
            continue
        # calculate mean of all lane endpoints
        pts = []
        for lid in junc.lane_ids:
            if lid in lanes_dict:
                ln = lanes_dict[lid]
                if ln.type == LaneType.LANE_TYPE_DRIVING:
                    pts += [(nd.x+x_offset, nd.y+y_offset) for nd in ln.center_line.nodes]
        if not pts:
            continue
        cx, cy = np.mean(pts, axis=0)
        ET.SubElement(nodes_root, "node", {"id": str(junc.id), "x": f"{cx:.2f}", "y": f"{cy:.2f}", "type": "priority"})

    road_map = {r.id: r for r in m.roads if r.id in valid_edge_ids}
    for junc in m.junctions:
        for grp in junc.driving_lane_groups:
            in_r, out_r = grp.in_road_id, grp.out_road_id
            if in_r not in road_map or out_r not in road_map:
                continue
            in_map = lane_index_map.get(in_r, {})
            out_map = lane_index_map.get(out_r, {})
            for clid in grp.lane_ids:
                if clid not in lanes_dict:
                    continue
                cl = lanes_dict[clid]
                if not cl.predecessors or not cl.successors:
                    continue
                pre, suc = cl.predecessors[0].id, cl.successors[0].id
                if pre in in_map and suc in out_map:
                    ET.SubElement(connections_root, "connection", {"from": str(in_r), "to": str(out_r), "fromLane": str(in_map[pre]), "toLane": str(out_map[suc])})

    ET.ElementTree(edges_root).write(output_edge, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(nodes_root).write(output_node, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(connections_root).write(output_connection, encoding="utf-8", xml_declaration=True)
    print(f"Files saved: {output_edge}, {output_node}, {output_connection}")


def is_sumo_installed() -> bool:
    return shutil.which("sumo") is not None


def run_netconvert(city: str) -> None:
    cmd = [
        "netconvert",
        f"--node-files={city}.nod.xml",
        f"--edge-files={city}.edg.xml",
        f"--connection-files={city}.con.xml",
        f"--output-file={city}.net.xml"
    ]
    subprocess.run(cmd, check=True)


def main() -> None:
    if not is_sumo_installed():
        print("[ERROR] SUMO is not installed on this system. Please install SUMO and ensure it is in the system's PATH.")
        sys.exit(1)
    city = sys.argv[1] if len(sys.argv)>1 else "Shaped"
    convert(
        map_file="map-16.pb",
        agent_file="null",
        output_edge=f"./{city}.edg.xml",
        output_node=f"./{city}.nod.xml",
        output_connection=f"./{city}.con.xml",
    )
    run_netconvert(city)

if __name__ == "__main__":
    main()
