#!/usr/bin/env python3
"""Run route planning tests with fixed Wuhan cases.

Usage:
    uv run python run_wuhan_net.py \
        --output-dir results/wuhan_tests/net \
        --cases case1 case3

    # Run all cases
    uv run python run_wuhan_net.py

    # Run single case with explicit coordinates
    uv run python run_wuhan_net.py \
        --start-lat 30.4907 --start-lon 114.5452 \
        --end-lat 30.4374 --end-lon 114.4171 \
        --output-dir results/wuhan_tests/net/custom
"""

import argparse
import os
import sys

# Ensure project root is on sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from route_planner import RoutePlanner


def generate_fixed_cases():
    """Return fixed Wuhan test cases."""
    case1 = {
        "name": "case1",
        "start": (30.4907, 114.5452),
        "end": (30.4374, 114.4171),
        "vias": [
            (30.4732, 114.4758), 
            (30.4179, 114.4490)],
        "distance": 20
    }
    case2 = {
        "name": "case2",
        "start": (30.4907, 114.5452),
        "end": (30.4419, 114.3776),
        "vias": [
            (30.4732, 114.4758),
            (30.4179, 114.4490),
            (30.4374, 114.4171),
            (30.5090, 114.4185),
            (30.5070, 114.3934),
        ],
        "distance": 40
    }
    case3 = {
        "name": "case3",
        "start": (30.4907, 114.5452),
        "end": (30.4852, 114.4758),
        "vias": [(30.4890, 114.5000)],
        "distance": 5
    }
    return [case1, case2, case3]


def load_net_network(net_file: str):
    """Load Net network from file.

    Args:
        net_file: Net路网文件路径

    Returns:
        (NetworkX graph, NetDataProcessor) tuple
    """
    from utils import NetDataProcessor

    net_processor = NetDataProcessor()
    G = net_processor.load_network_from_net(net_file)
    return G, net_processor


def find_route_nodes(net_processor, G, start_lat: float, start_lon: float,
                     end_lat: float, end_lon: float):
    """Find start and end nodes in the network.

    Args:
        net_processor: NetDataProcessor instance with boundary params
        G: NetworkX graph
        start_lat: 起点纬度
        start_lon: 起点经度
        end_lat: 终点纬度
        end_lon: 终点经度

    Returns:
        (start_node, end_node) tuple
    """
    start_node = net_processor.find_nearest_node(G, start_lat, start_lon)
    end_node = net_processor.find_nearest_node(G, end_lat, end_lon)
    return start_node, end_node


def calculate_baseline(G, start_node, end_node):
    """Calculate baseline shortest path.

    Args:
        G: NetworkX graph
        start_node: 起点节点
        end_node: 终点节点

    Returns:
        dict with route, distance, edge_count, and congestion_percentage
    """
    import networkx as nx
    from utils import OSMDataProcessor

    # Calculate shortest path
    route = nx.shortest_path(G, start_node, end_node, weight='length')
    distance = sum(G[route[i]][route[i+1]][0].get('length', 100)
                   for i in range(len(route)-1))

    # Calculate congestion score
    osm_processor = OSMDataProcessor()
    congestion_scores = osm_processor.calculate_congestion_score(G)
    stats = osm_processor.calculate_route_stats(G, route, congestion_scores)

    return {
        'nodes': route,
        'total_distance': distance,
        'edge_count': len(route) - 1,
        'congestion_percentage': stats['congestion_percentage']
    }


def route_nodes_to_xy_coords(G, route):
    """Convert node route to XY coordinates.

    Args:
        G: NetworkX graph
        route: List of node IDs

    Returns:
        (x_list, y_list) tuple
    """
    x_list = []
    y_list = []
    for node in route:
        if node in G.nodes:
            node_data = G.nodes[node]
            x, y = node_data.get('x', 0), node_data.get('y', 0)
            x_list.append(x)
            y_list.append(y)
    return x_list, y_list


def generate_visualization(net_file: str, output_dir: str,
                           start_node_id: str, end_node_id: str,
                           via_node_ids: list, G,
                           route_result: dict):
    """Generate visualization image for GA optimized route.

    Args:
        net_file: Net路网文件路径
        output_dir: 输出目录
        start_node_id: 起点节点ID
        end_node_id: 终点节点ID
        via_node_ids: 途经点节点ID列表
        G: NetworkX graph
        route_result: GA优化路径结果
    """
    try:
        import sumolib
        import matplotlib.pyplot as plt

        # Read Net network
        net = sumolib.net.readNet(net_file)

        # Extract network data
        edge_count = 0
        edge_x_list = []
        edge_y_list = []
        for edge in net.getEdges():
            if edge.getID().startswith(':'):
                continue
            shape = edge.getShape()
            if len(shape) >= 2:
                edge_x_list.append([p[0] for p in shape])
                edge_y_list.append([p[1] for p in shape])
                edge_count += 1

        node_x = [node.getCoord()[0] for node in net.getNodes()]
        node_y = [node.getCoord()[1] for node in net.getNodes()]

        # Get start/end/via coordinates from network nodes (not from lat/lon conversion)
        start_data = G.nodes[start_node_id]
        start_x, start_y = start_data.get('x', 0), start_data.get('y', 0)

        end_data = G.nodes[end_node_id]
        end_x, end_y = end_data.get('x', 0), end_data.get('y', 0)

        via_x = []
        via_y = []
        if via_node_ids:
            for node_id in via_node_ids:
                node_data = G.nodes[node_id]
                via_x.append(node_data.get('x', 0))
                via_y.append(node_data.get('y', 0))

        # Get route coordinates
        route_x, route_y = route_nodes_to_xy_coords(G, route_result['nodes'])

        print(f"  绘制了 {edge_count} 条边, {len(node_x)} 个节点")

        # Generate single route visualization
        vis_file = os.path.join(output_dir, "route_visualization.png")
        fig, ax = plt.subplots(figsize=(14, 12))

        for ex, ey in zip(edge_x_list, edge_y_list):
            ax.plot(ex, ey, 'gray', linewidth=0.5, alpha=0.4)
        ax.scatter(node_x, node_y, c='black', s=2, alpha=0.3, zorder=1)

        if route_x and route_y:
            ax.plot(route_x, route_y, 'r-', linewidth=2.5, alpha=0.9,
                    label=f"GA Optimized ({route_result['edge_count']} edges, "
                          f"{route_result['total_distance']:.0f}m)")

        ax.scatter([start_x], [start_y], marker='^', c='green', s=250, zorder=5,
                   label='Start', edgecolors='darkgreen', linewidths=2)
        ax.scatter([end_x], [end_y], marker='s', c='red', s=250, zorder=5,
                   label='End', edgecolors='darkred', linewidths=2)
        if via_x:
            ax.scatter(via_x, via_y, marker='o', c='orange', s=180, zorder=5,
                       label='Via Points', edgecolors='darkorange', linewidths=2)

        ax.set_xlabel('X (meters)', fontsize=12)
        ax.set_ylabel('Y (meters)', fontsize=12)
        ax.set_title('Net Route Planning (GA Optimized)\n'
                     f'Start: Node {start_node_id} -> End: Node {end_node_id}\n'
                     f"Distance: {route_result['total_distance']:.0f}m, "
                     f"Congestion: {route_result['congestion_percentage']:.1f}%",
                     fontsize=12)
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_aspect('equal')

        stats_text = (f"Route Statistics:\n"
                      f"Nodes: {len(route_result['nodes'])}\n"
                      f"Edges: {route_result['edge_count']}\n"
                      f"Distance: {route_result['total_distance']:.0f}m\n"
                      f"Congestion: {route_result['congestion_percentage']:.1f}%")
        ax.text(0.02, 0.98, stats_text, transform=ax.transAxes,
                fontsize=9, verticalalignment='top',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8),
                family='monospace')

        plt.tight_layout()
        plt.savefig(vis_file, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  路径图保存到: {vis_file}")

    except ImportError:
        print("  警告: sumolib 或 matplotlib 未安装，跳过可视化")
    except Exception as e:
        print(f"  警告: 可视化生成失败: {e}")
        import traceback
        traceback.print_exc()


def generate_comparison_visualization(net_file: str, output_dir: str,
                                      start_node_id: str, end_node_id: str,
                                      via_node_ids: list, G,
                                      baseline_result: dict,
                                      optimized_result: dict):
    """Generate comparison visualization: baseline vs optimized route.

    Args:
        net_file: Net路网文件路径
        output_dir: 输出目录
        start_node_id: 起点节点ID
        end_node_id: 终点节点ID
        via_node_ids: 途经点节点ID列表
        G: NetworkX graph
        baseline_result: Baseline最短路径结果
        optimized_result: GA优化路径结果
    """
    try:
        import sumolib
        import matplotlib.pyplot as plt

        # Read Net network
        net = sumolib.net.readNet(net_file)

        # Extract network data
        edge_count = 0
        edge_x_list = []
        edge_y_list = []
        for edge in net.getEdges():
            if edge.getID().startswith(':'):
                continue
            shape = edge.getShape()
            if len(shape) >= 2:
                edge_x_list.append([p[0] for p in shape])
                edge_y_list.append([p[1] for p in shape])
                edge_count += 1

        node_x = [node.getCoord()[0] for node in net.getNodes()]
        node_y = [node.getCoord()[1] for node in net.getNodes()]

        # Get start/end/via coordinates from network nodes (not from lat/lon conversion)
        start_data = G.nodes[start_node_id]
        start_x, start_y = start_data.get('x', 0), start_data.get('y', 0)

        end_data = G.nodes[end_node_id]
        end_x, end_y = end_data.get('x', 0), end_data.get('y', 0)

        via_x = []
        via_y = []
        if via_node_ids:
            for node_id in via_node_ids:
                node_data = G.nodes[node_id]
                via_x.append(node_data.get('x', 0))
                via_y.append(node_data.get('y', 0))

        # Get route coordinates
        baseline_x, baseline_y = route_nodes_to_xy_coords(G, baseline_result['nodes'])
        optimized_x, optimized_y = route_nodes_to_xy_coords(G, optimized_result['nodes'])

        print(f"  绘制了 {edge_count} 条边, {len(node_x)} 个节点")

        # Generate comparison visualization (side by side)
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 10))

        # Plot background network on both axes
        for ax in [ax1, ax2]:
            for ex, ey in zip(edge_x_list, edge_y_list):
                ax.plot(ex, ey, 'gray', linewidth=0.5, alpha=0.4)
            ax.scatter(node_x, node_y, c='black', s=2, alpha=0.3, zorder=1)

        # Left plot: Baseline (Shortest Path)
        if baseline_x and baseline_y:
            ax1.plot(baseline_x, baseline_y, 'b-', linewidth=2.5, alpha=0.9,
                     label=f"Baseline ({baseline_result['edge_count']} edges, "
                           f"{baseline_result['total_distance']:.0f}m)")

        ax1.scatter([start_x], [start_y], marker='^', c='green', s=250, zorder=5,
                    label='Start', edgecolors='darkgreen', linewidths=2)
        ax1.scatter([end_x], [end_y], marker='s', c='red', s=250, zorder=5,
                    label='End', edgecolors='darkred', linewidths=2)
        if via_x:
            ax1.scatter(via_x, via_y, marker='o', c='orange', s=180, zorder=5,
                        label='Via Points', edgecolors='darkorange', linewidths=2)

        ax1.set_xlabel('X (meters)', fontsize=11)
        ax1.set_ylabel('Y (meters)', fontsize=11)
        ax1.set_title('Baseline (Shortest Path)\n'
                      f'Start: Node {start_node_id} -> End: Node {end_node_id}\n'
                      f"Distance: {baseline_result['total_distance']:.0f}m, "
                      f"Congestion: {baseline_result['congestion_percentage']:.1f}%",
                      fontsize=12, color='blue')
        ax1.legend(loc='best', fontsize=9)
        ax1.grid(True, alpha=0.3)
        ax1.set_aspect('equal')

        # Right plot: Optimized Route
        if optimized_x and optimized_y:
            ax2.plot(optimized_x, optimized_y, 'r-', linewidth=2.5, alpha=0.9,
                     label=f"GA Optimized ({optimized_result['edge_count']} edges, "
                           f"{optimized_result['total_distance']:.0f}m)")

        ax2.scatter([start_x], [start_y], marker='^', c='green', s=250, zorder=5,
                    label='Start', edgecolors='darkgreen', linewidths=2)
        ax2.scatter([end_x], [end_y], marker='s', c='red', s=250, zorder=5,
                    label='End', edgecolors='darkred', linewidths=2)
        if via_x:
            ax2.scatter(via_x, via_y, marker='o', c='orange', s=180, zorder=5,
                        label='Via Points', edgecolors='darkorange', linewidths=2)

        ax2.set_xlabel('X (meters)', fontsize=11)
        ax2.set_ylabel('Y (meters)', fontsize=11)
        ax2.set_title('GA Optimized Route\n'
                      f'Start: Node {start_node_id} -> End: Node {end_node_id}\n'
                      f"Distance: {optimized_result['total_distance']:.0f}m, "
                      f"Congestion: {optimized_result['congestion_percentage']:.1f}%",
                      fontsize=12, color='red')
        ax2.legend(loc='best', fontsize=9)
        ax2.grid(True, alpha=0.3)
        ax2.set_aspect('equal')

        # Add overall title
        improvement_dist = baseline_result['total_distance'] - optimized_result['total_distance']
        improvement_pct = (improvement_dist / baseline_result['total_distance'] * 100) if baseline_result['total_distance'] > 0 else 0
        improvement_cong = optimized_result['congestion_percentage'] - baseline_result['congestion_percentage']

        fig.suptitle(f'Route Comparison: Baseline vs GA Optimized\n'
                     f'Distance Improvement: {improvement_dist:.0f}m ({improvement_pct:.1f}%) | '
                     f'Congestion Change: {improvement_cong:+.1f}%',
                     fontsize=14, fontweight='bold')

        # Add comparison stats text
        stats_text = (f"Baseline:\n"
                      f"  Distance: {baseline_result['total_distance']:.0f}m\n"
                      f"  Edges: {baseline_result['edge_count']}\n"
                      f"  Congestion: {baseline_result['congestion_percentage']:.1f}%\n\n"
                      f"GA Optimized:\n"
                      f"  Distance: {optimized_result['total_distance']:.0f}m\n"
                      f"  Edges: {optimized_result['edge_count']}\n"
                      f"  Congestion: {optimized_result['congestion_percentage']:.1f}%")
        fig.text(0.02, 0.02, stats_text, fontsize=9, verticalalignment='bottom',
                 bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.9),
                 family='monospace')

        plt.tight_layout(rect=[0, 0.08, 1, 0.95])
        comp_file = os.path.join(output_dir, "route_comparison.png")
        plt.savefig(comp_file, dpi=150, bbox_inches='tight')
        plt.close()
        print(f"  对比图保存到: {comp_file}")

    except ImportError:
        print("  警告: sumolib 或 matplotlib 未安装，跳过对比可视化")
    except Exception as e:
        print(f"  警告: 对比可视化生成失败: {e}")
        import traceback
        traceback.print_exc()


def run_single_test(net_file: str, start_lat: float, start_lon: float,
                    end_lat: float, end_lon: float, output_dir: str,
                    via_points=None, distance=None, generations=10):
    """
    Run a single Net route planning test.

    Args:
        net_file: Net路网文件路径
        start_lat: 起点纬度
        start_lon: 起点经度
        end_lat: 终点纬度
        end_lon: 终点经度
        output_dir: 输出目录
        via_points: 途经点列表 [(lat, lon), ...]
        distance: 目标距离约束（公里）
        generations: 遗传算法迭代次数
    """
    os.makedirs(output_dir, exist_ok=True)
    via_points = via_points or []

    print("  加载Net路网...")
    G, net_processor = load_net_network(net_file)

    print("  查找起点和终点节点...")
    start_node, end_node = find_route_nodes(net_processor, G, start_lat, start_lon, end_lat, end_lon)

    print("  计算baseline最短路径...")
    baseline_result = calculate_baseline(G, start_node, end_node)

    # Create Args object for RoutePlanner
    class Args:
        pass

    route_args = Args()
    route_args.start_lat = start_lat
    route_args.start_lon = start_lon
    route_args.end_lat = end_lat
    route_args.end_lon = end_lon
    route_args.intermediate_lats = [v[0] for v in via_points]
    route_args.intermediate_lons = [v[1] for v in via_points]
    route_args.distance = distance
    route_args.generations = generations
    route_args.record_interval = generations // 2 if generations > 1 else 1
    route_args.net_file = net_file
    route_args.local_map = None
    route_args.data_dir = "data"
    route_args.margin_km = 1.0
    route_args.city = "Net Network"
    route_args.start = None
    route_args.end = None
    route_args.via = []

    print("  执行GA路径规划...")
    planner = RoutePlanner()
    route_result = planner.plan_route(route_args)

    # Save result
    result_file = os.path.join(output_dir, "route_planning.json")
    planner.save_results(route_result, result_file)
    print(f"  结果已保存到: {result_file}")

    # Get via node IDs from via_points coordinates
    via_node_ids = []
    if via_points:
        for lat, lon in via_points:
            node_id = net_processor.find_nearest_node(G, lat, lon)
            if node_id:
                via_node_ids.append(node_id)

    print(f"  途经点节点ID: {via_node_ids}")

    # Generate visualization
    print("  生成可视化...")
    # Generate single route visualization (optimized route only)
    generate_visualization(
        net_file=net_file,
        output_dir=output_dir,
        start_node_id=start_node,
        end_node_id=end_node,
        via_node_ids=via_node_ids,
        G=G,
        route_result=route_result['route']
    )

    # Generate comparison visualization (baseline vs optimized)
    print("  生成对比图...")
    generate_comparison_visualization(
        net_file=net_file,
        output_dir=output_dir,
        start_node_id=start_node,
        end_node_id=end_node,
        via_node_ids=via_node_ids,
        G=G,
        baseline_result=baseline_result,
        optimized_result=route_result['route']
    )

    # Print summary
    print(f"\n  规划结果摘要:")
    print(f"  Baseline (最短路径):")
    print(f"    节点数: {len(baseline_result['nodes'])}")
    print(f"    边数: {baseline_result['edge_count']}")
    print(f"    总距离: {baseline_result['total_distance']:.2f} 米")
    print(f"    拥堵系数: {baseline_result['congestion_percentage']:.1f}%")
    print(f"  GA 优化路径:")
    print(f"    节点数: {len(route_result['route']['nodes'])}")
    print(f"    边数: {route_result['route']['edge_count']}")
    print(f"    总距离: {route_result['route']['total_distance']:.2f} 米")
    print(f"    拥堵系数: {route_result['route']['congestion_percentage']:.1f}%")

    return route_result, baseline_result, via_node_ids


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Net-based route planning tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--net-file", default="data/wuhan_core.net.xml",
                        help='Net路网文件路径（默认: data/wuhan_core.net.xml）')
    parser.add_argument("--output-dir", default="results/",
                        help='输出目录（默认: results/）')
    parser.add_argument("--cases", nargs='+', default=None,
                        help="选择运行的测试用例，例如: --cases case1 case3 或 --cases case3")
    parser.add_argument("--start-lat", type=float,
                        help='起点纬度（单独运行时使用）')
    parser.add_argument("--start-lon", type=float,
                        help='起点经度（单独运行时使用）')
    parser.add_argument("--end-lat", type=float,
                        help='终点纬度（单独运行时使用）')
    parser.add_argument("--end-lon", type=float,
                        help='终点经度（单独运行时使用）')
    parser.add_argument("--generations", type=int, default=100,
                        help='遗传算法迭代次数（默认: 100）')
    parser.add_argument("--force-recompute", action="store_true",
                        help='强制重新计算')

    args = parser.parse_args()

    # Run single test with explicit coordinates
    if all(v is not None for v in [args.start_lat, args.start_lon, args.end_lat, args.end_lon]):
        output_dir = os.path.join(args.output_dir, "custom")
        run_single_test(
            net_file=args.net_file,
            start_lat=args.start_lat,
            start_lon=args.start_lon,
            end_lat=args.end_lat,
            end_lon=args.end_lon,
            output_dir=output_dir,
            via_points=None,
            distance=None,
            generations=args.generations
        )
        return

    # Run fixed test cases
    all_cases = generate_fixed_cases()
    case_names = [c["name"] for c in all_cases]

    if args.cases and "all" not in args.cases:
        selected_cases = [c for c in all_cases if c["name"] in args.cases]
        invalid_cases = set(args.cases) - set(case_names)
        if invalid_cases:
            print(f"警告: 无效的 case 名称: {invalid_cases}")
            print(f"有效选项: {case_names}")
            return
    else:
        selected_cases = all_cases

    print("=" * 60)
    print("Net路网路径规划测试")
    print("=" * 60)
    print(f"Net路网: {args.net_file}")
    print(f"输出目录: {args.output_dir}")
    print(f"测试用例: {[c['name'] for c in selected_cases]}")
    print("=" * 60)

    # 导入 metric 计算函数
    from utils import calculate_via_satisfaction, calculate_distance_satisfaction

    # Run each test case
    all_results = []
    for case in selected_cases:
        print(f"\n运行测试用例: {case['name']}")
        print(f"  起点: ({case['start'][0]}, {case['start'][1]})")
        print(f"  终点: ({case['end'][0]}, {case['end'][1]})")
        if case['vias']:
            print(f"  途经点: {case['vias']}")
        print(f"  目标距离: {case.get('distance', '无')} km")

        output_dir = os.path.join(args.output_dir, case["name"])

        # Check if result exists
        result_file = os.path.join(output_dir, "route_planning.json")
        if os.path.exists(result_file) and not args.force_recompute:
            print(f"  结果已存在，跳过（使用 --force-recompute 重新计算）")
            import json
            with open(result_file, 'r') as f:
                case_result = json.load(f)
            baseline_result = None
            via_node_ids = []
        else:
            try:
                case_result, baseline_result, via_node_ids = run_single_test(
                    net_file=args.net_file,
                    start_lat=case['start'][0],
                    start_lon=case['start'][1],
                    end_lat=case['end'][0],
                    end_lon=case['end'][1],
                    output_dir=output_dir,
                    via_points=case['vias'],
                    distance=case.get('distance'),
                    generations=args.generations
                )
            except RuntimeError as e:
                print(f"  测试失败: {e}")
                case_result = None
                baseline_result = None

        if case_result and baseline_result:
            target_distance = case.get('distance')

            # Baseline 结果
            all_results.append({
                "case": case["name"],
                "method": "baseline",
                "distance": baseline_result['total_distance'],
                "edge_count": baseline_result['edge_count'],
                "congestion_score": baseline_result['congestion_percentage'],
                "distance_satisfaction": calculate_distance_satisfaction(
                    baseline_result['total_distance'], target_distance),
                "via_satisfaction": calculate_via_satisfaction(
                    baseline_result['nodes'], via_node_ids),
                "output_dir": output_dir
            })

            # GA 结果
            all_results.append({
                "case": case["name"],
                "method": "GA",
                "distance": case_result['route']['total_distance'],
                "edge_count": case_result['route']['edge_count'],
                "congestion_score": case_result['route']['congestion_percentage'],
                "distance_satisfaction": calculate_distance_satisfaction(
                    case_result['route']['total_distance'], target_distance),
                "via_satisfaction": calculate_via_satisfaction(
                    case_result['route']['nodes'], via_node_ids),
                "output_dir": output_dir
            })

    # Print summary
    if all_results:
        print("\n" + "=" * 92)
        print("测试结果总结")
        print("=" * 92)
        print(f"{'Case':<8} {'方法':<10} {'距离(m)':>12} {'边数':>8} {'拥堵程度':>12} {'距离满足度':>14} {'途经点满足度':>14}")
        print("-" * 92)

        # 构建结果表格字符串
        lines = []
        for r in all_results:
            line = f"{r['case']:<8} {r['method']:<10} {r['distance']:>12.2f} {r['edge_count']:>8} " \
                   f"{r['congestion_score']:>10.1f}%   {r['distance_satisfaction']:>12.1f}%   " \
                   f"{r['via_satisfaction']:>12.1f}%"
            print(line)
            lines.append(line)

        print("-" * 92)
        print(f"总计: {len(all_results)} 条记录 ({len(selected_cases)} 个测试用例)")

        # 写入结果文件到当前case的输出目录（与route_comparison.png一致）
        output_dir = os.path.join(args.output_dir, case["name"])
        output_file = os.path.join(output_dir, "summary.txt")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("测试结果总结\n")
            f.write("=" * 92 + "\n")
            f.write(f"{'Case':<8} {'方法':<10} {'距离(m)':>12} {'边数':>8} {'拥堵程度':>12} {'距离满足度':>14} {'途经点满足度':>14}\n")
            f.write("-" * 92 + "\n")
            for line in lines:
                f.write(line + "\n")
            f.write("-" * 92 + "\n")
            f.write(f"总计: {len(all_results)} 条记录 ({len(selected_cases)} 个测试用例)\n")
        print(f"结果已保存到: {output_file}")


if __name__ == "__main__":
    main()
