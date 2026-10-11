import xml.etree.ElementTree as ET
import networkx as nx
import json


def get_path(hospitals = ['200002126', '200001486', '200001174', '200000268', '200000593', '200002565'], accidents = ['200002141', '200001458', '200001481', '200001367', '200001452']):

    # 解析 XML
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
        results[hospital] = {}
        for accident in accidents:
            print(f"Finding paths from {hospital} to {accident}...")
            
            try:
                # 获取所有最短路径
                all_paths = list(nx.all_shortest_paths(G, hospital, accident))
                results[hospital][accident] = [
                    {
                        'rank': i + 1,
                        'path': path,
                        'num_edges': len(path) - 1
                    }
                    for i, path in enumerate(all_paths[:5])  # 最多保存5条
                ]
                print(f"  Found {len(all_paths)} shortest paths, saved {min(5, len(all_paths))}")
            except (nx.NetworkXNoPath, nx.NodeNotFound) as e:
                results[hospital][accident] = {'error': str(e)}
                print(f"  Error: {e}")

    # 保存结果
    with open('shortest_paths.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print("\nDone! Results saved to shortest_paths.json")