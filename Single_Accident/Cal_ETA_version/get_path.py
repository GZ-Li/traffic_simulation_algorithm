import xml.etree.ElementTree as ET
import networkx as nx
import json


def get_route_to_acc(hospitals = ['200001174', '200000268', '200000593', '200002565'], accident = '200002141', output_file = 'shortest_paths.json'):

    # 解析由PB转换得到的SUMO路网
    tree = ET.parse('Shaped.net.xml')
    root = tree.getroot()

    # 构建有向图
    G = nx.DiGraph()

    # 添加边
    for connection in root.findall('.//connection'):
        from_edge = connection.get('from')
        to_edge = connection.get('to')
        
        if from_edge.startswith(':') or to_edge.startswith(':'):
            continue
        
        G.add_edge(from_edge, to_edge)

    print(f"Network: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # 求最短路径
    results = {}
    for hospital in hospitals:
        print(f"Finding paths from {hospital} to {accident}...")
        
        try:
            # 获取所有最短路径
            all_paths = list(nx.all_shortest_paths(G, hospital, accident))
            results[hospital] = [
                {
                    'rank': i + 1,
                    'path': path,
                    'num_edges': len(path) - 1
                }
                for i, path in enumerate(all_paths[:5])  # 最多保存5条
            ]
            print(f"  Found {len(all_paths)} shortest paths, saved {min(5, len(all_paths))}")
        except (nx.NetworkXNoPath, nx.NodeNotFound) as e:
            results[hospital] = {'error': str(e)}
            print(f"  Error: {e}")

    # 保存结果
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\nDone! Results saved to shortest_paths.json")
    

def main():
    import argparse
    import ast
    parser = argparse.ArgumentParser(description="Compute shortest paths from hospitals to an accident node.")
    parser.add_argument("--hospitals", type=str, required=True,
                        help="Hospital nodes list, e.g., \"['200001174','200000268']\"")
    parser.add_argument("--accident", type=str, required=True,
                        help="Accident node ID, e.g., '200002141'")
    parser.add_argument("--output_file", type=str, default="shortest_paths.json",
                        help="Output JSON file path")
    args = parser.parse_args()

    # 将命令行字符串解析为列表
    try:
        hospitals_list = ast.literal_eval(args.hospitals)
        if not isinstance(hospitals_list, list):
            raise ValueError
    except:
        print("Error: --hospitals must be a list, e.g., \"['200001174','200000268']\"")
        return

    get_route_to_acc(hospitals_list, args.accident, args.output_file)

if __name__ == "__main__":
    main()

# python3 get_path.py \
#     --hospitals "['200001174','200000268','200000593','200002565']" \
#     --accident "200002141" \
#     --output_file "shortest_paths.json"