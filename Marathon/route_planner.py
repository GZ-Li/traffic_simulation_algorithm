"""
基于OR-Tools和遗传算法的智能路线规划器
"""

import sys
import argparse
import json
import time
from typing import List, Tuple, Dict, Optional
import numpy as np
from datetime import datetime

# OR-Tools相关导入
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

# 本地模块导入
from utils import OSMDataProcessor, NetDataProcessor, setup_matplotlib_for_plotting, calculate_route_metrics
import osmnx as ox
import networkx as nx
import matplotlib.pyplot as plt

class GeneticOptimizer:
    """遗传算法优化器"""
    
    def __init__(self, population_size: int = 50, generations: int = 100,
                 mutation_rate: float = 0.1, elite_size: int = 10, record_interval: int = 50):
        """
        初始化遗传算法参数

        Args:
            population_size: 种群大小
            generations: 进化代数
            mutation_rate: 变异概率
            elite_size: 精英数量
            record_interval: 详细路径记录间隔（每多少代记录一次）
        """
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.elite_size = elite_size
        self.record_interval = record_interval
        self.optimization_history = []
        self._last_recorded_path = None  # 上一次记录的路径
        
    def _is_path_changed(self, current_path: List[int]) -> bool:
        """
        检查当前路径是否与上一次记录的路径不同

        Args:
            current_path: 当前路径节点列表

        Returns:
            True if path changed, False otherwise
        """
        if self._last_recorded_path is None:
            return True  # 第一次记录

        if len(current_path) != len(self._last_recorded_path):
            return True  # 路径长度变化

        # 比较节点序列
        return current_path != self._last_recorded_path

    def _is_valid_edge(self, G: nx.MultiDiGraph, u, v) -> bool:
        """检查两个节点之间是否有直接连边"""
        return G.has_edge(u, v)

    def _validate_path(self, G: nx.MultiDiGraph, path: List[int],
                       start_node: int, end_node: int,
                       intermediate_nodes: List[int]) -> bool:
        """验证路径的有效性"""
        if len(path) < 2:
            return False
        if path[0] != start_node or path[-1] != end_node:
            return False
        # 检查所有相邻节点之间是否有有效边
        for i in range(len(path) - 1):
            if not self._is_valid_edge(G, path[i], path[i+1]):
                return False
        # 检查中间节点是否按顺序出现
        current_idx = 0
        for node in intermediate_nodes:
            try:
                current_idx = path.index(node, current_idx)
            except ValueError:
                return False
        return True

    def create_individual(self, start_node: int, end_node: int, intermediate_nodes: List[int],
                         G: nx.MultiDiGraph) -> List[int]:
        """
        创建个体（路径）- 修复版：正确连接所有途经点

        Args:
            start_node: 起始节点
            end_node: 结束节点
            intermediate_nodes: 中间必经节点
            G: 路网图

        Returns:
            完整的路径节点列表
        """
        # 构建完整的途经点列表：起点 + 中间点 + 终点
        all_waypoints = [start_node] + intermediate_nodes + [end_node]

        complete_path = []

        # 依次连接相邻的途经点
        for i in range(len(all_waypoints) - 1):
            current_point = all_waypoints[i]
            next_point = all_waypoints[i + 1]

            try:
                # 计算两点间的最短路径
                segment_path = nx.shortest_path(G, current_point, next_point, weight='length')

                # 避免重复添加连接点
                if complete_path:
                    complete_path.extend(segment_path[1:])  # 跳过第一个节点（已存在）
                else:
                    complete_path = segment_path

            except nx.NetworkXNoPath:
                # 如果无法找到路径，添加惩罚并返回部分路径
                print(f"警告: 无法从节点 {current_point} 找到到节点 {next_point} 的路径")
                complete_path.append(next_point)

        return complete_path
    
    def create_population(self, start_node: int, end_node: int, intermediate_nodes: List[int],
                         G: nx.MultiDiGraph, initial_path: List[int] = None) -> List[List[int]]:
        """
        创建初始种群 (支持传入初始优选路径)

        Args:
            start_node: 起始节点
            end_node: 结束节点
            intermediate_nodes: 中间节点列表
            G: 路网图
            initial_path: OR-Tools计算的初始优化路径（可选）

        Returns:
            种群列表
        """
        population = []
        int_nodes = intermediate_nodes if intermediate_nodes else []

        # 1. 决定基础路径 (种子)
        if initial_path is not None and len(initial_path) > 0:
            # 如果有OR-Tools算好的路径，直接使用它作为最强个体
            base_path = initial_path.copy()
            print(f"使用OR-Tools路径作为种群种子，长度: {len(base_path)}")
        else:
            # 否则按原始顺序生成
            base_path = self.create_individual(start_node, end_node, int_nodes, G)
            print(f"使用默认顺序生成种群种子，长度: {len(base_path)}")

        # 验证基础路径有效性
        if G is not None and not self._validate_path(G, base_path, start_node, end_node, int_nodes):
            print("警告: 基础路径无效，重新生成...")
            base_path = self.create_individual(start_node, end_node, int_nodes, G)

        population.append(base_path)

        # 2. 基于基础路径生成变体
        for _ in range(self.population_size - 1):
            individual = base_path.copy()

            # 变异逻辑：在现有路径基础上微调
            if len(individual) > 3:
                # 随机选择是否进行变异，传递途经点参数以保护约束
                if np.random.random() < 0.5:
                    individual = self.mutate(individual, G, probability=0.8,
                                            start_node=start_node,
                                            end_node=end_node,
                                            intermediate_nodes=int_nodes)

            # 验证个体有效性，无效则重新生成
            if G is not None and not self._validate_path(G, individual, start_node, end_node, int_nodes):
                individual = self.create_individual(start_node, end_node, int_nodes, G)

            population.append(individual)

        return population
    
    def evaluate_fitness(self, individual: List[int], G: nx.MultiDiGraph,
                        congestion_scores: Dict, target_distance: Optional[float] = None,
                        start_node: int = None, end_node: int = None,
                        intermediate_nodes: List[int] = None) -> float:
        """
        计算个体适应度 - 修复版：加强途经点约束检查

        使用拥堵系数：拥堵越高，适应度越低（应选择更畅通的路线）

        Args:
            individual: 个体路径
            G: 路网图
            congestion_scores: 拥堵系数字典
            target_distance: 目标距离
            start_node: 起始节点（用于约束验证）
            end_node: 结束节点（用于约束验证）
            intermediate_nodes: 中间必经节点列表（用于约束验证）

        Returns:
            适应度分数（越高越好）
        """
        if len(individual) < 2:
            return 0.0

        # 硬约束检查：验证途经点约束
        if start_node is not None and end_node is not None and intermediate_nodes is not None:
            # 检查起点和终点
            if individual[0] != start_node:
                return 0.001  # 严重惩罚：起点错误
            if individual[-1] != end_node:
                return 0.001  # 严重惩罚：终点错误

            # 检查所有中间途经点是否按顺序出现
            current_index = 0
            for waypoint in intermediate_nodes:
                try:
                    current_index = individual.index(waypoint, current_index)
                except ValueError:
                    return 0.001  # 严重惩罚：缺失途经点

        # 使用通用函数计算路径统计
        route_stats = calculate_route_metrics(individual, G, congestion_scores)
        total_distance = route_stats['total_distance']
        avg_congestion_score = route_stats['avg_congestion_score']

        # 距离适应度：使用高斯函数，更平滑
        distance_score = 1.0
        if target_distance is not None:
            distance_ratio = (total_distance - target_distance) / target_distance
            if distance_ratio >= 0:
                # 允许+30%偏差，正偏差惩罚较轻
                distance_score = np.exp(-0.5 * (distance_ratio / 0.3) ** 2)
            else:
                # -10%以内，负偏差惩罚较重
                distance_score = np.exp(-0.5 * (distance_ratio / 0.1) ** 2)
        else:
            # 无目标距离时，使用归一化的倒数（基于种群范围）
            # 这会在后续optimize中设置
            distance_score = 1.0

        # 拥堵适应度：拥堵越高，得分越低
        if total_distance > 0:
            # avg_congestion_score 已在上面从 route_stats 获取
            # 反转：低拥堵 = 高适应度
            congestion_score_normalized = 1.0 - avg_congestion_score
        else:
            congestion_score_normalized = 0.5

        # 自适应权重：根据拥堵改善情况调整
        # 如果拥堵较低，增大距离权重；如果拥堵较高，增大畅通权重
        if avg_congestion_score < 0.3:
            # 已经很畅通了，优先优化距离
            weight_distance, weight_congestion = 0.6, 0.4
        elif avg_congestion_score < 0.5:
            # 中等拥堵，平衡权重
            weight_distance, weight_congestion = 0.4, 0.6
        else:
            # 高拥堵，优先改善拥堵
            weight_distance, weight_congestion = 0.3, 0.7

        # 综合适应度
        fitness = distance_score * weight_distance + congestion_score_normalized * weight_congestion

        return fitness
    
    def select_parents(self, population: List[List[int]], fitness_scores: List[float],
                      generation: int = 0, total_generations: int = 100) -> List[List[int]]:
        """选择父母 - 锦标赛选择 + 自适应选择压力

        Args:
            population: 种群
            fitness_scores: 适应度分数
            generation: 当前代数（用于自适应选择压力）
            total_generations: 总代数
        """
        # 自适应锦标赛大小：随代数增加而增大
        progress = generation / total_generations if total_generations > 0 else 0
        tournament_size = 3 + int(5 * progress)  # 3~8

        parents = []
        pop_size = len(population)

        for _ in range(pop_size):
            # 随机选择锦标赛参与者
            tournament_idx = np.random.choice(pop_size, tournament_size, replace=False)
            tournament_fitness = [fitness_scores[i] for i in tournament_idx]

            # 选择适应度最高的个体
            winner_idx = tournament_idx[np.argmax(tournament_fitness)]
            parents.append(population[winner_idx].copy())

        return parents
    
    def crossover(self, parent1: List[int], parent2: List[int],
                  start_node: int = None, end_node: int = None,
                  intermediate_nodes: List[int] = None, G: nx.MultiDiGraph = None) -> Tuple[List[int], List[int]]:
        """
        交叉操作 - 修复版：保持途经点约束

        Args:
            parent1: 父代路径1
            parent2: 父代路径2
            start_node: 起始节点（用于验证）
            end_node: 结束节点（用于验证）
            intermediate_nodes: 中间必经节点列表
            G: 路网图（用于重新生成路径）
        """
        if len(parent1) < 3 or len(parent2) < 3:
            return parent1.copy(), parent2.copy()

        # 保持起点和终点不变，只在中间部分进行交叉
        start = start_node if start_node is not None else parent1[0]
        end = end_node if end_node is not None else parent1[-1]

        # 找到起始和结束节点在路径中的索引
        try:
            start_idx1 = parent1.index(start) if start in parent1 else 0
            end_idx1 = parent1.index(end) if end in parent1 else len(parent1) - 1
            start_idx2 = parent2.index(start) if start in parent2 else 0
            end_idx2 = parent2.index(end) if end in parent2 else len(parent2) - 1

            # 提取中间部分
            middle1 = parent1[start_idx1 + 1:end_idx1]
            middle2 = parent2[start_idx2 + 1:end_idx2]

            # 简单交叉：交换中间部分
            child1_middle = middle1[:len(middle1)//2] + middle2[len(middle2)//2:]
            child2_middle = middle2[:len(middle2)//2] + middle1[len(middle1)//2:]

            # 重新构建完整路径
            child1 = [start] + child1_middle + [end]
            child2 = [start] + child2_middle + [end]

        except (ValueError, nx.NetworkXNoPath):
            # 如果验证失败，返回父代的副本
            return parent1.copy(), parent2.copy()

        # 验证路径有效性（严格版本：检查所有相邻节点之间都有有效边）
        if G is not None:
            int_nodes = intermediate_nodes if intermediate_nodes else []
            if not self._validate_path(G, child1, start, end, int_nodes):
                child1 = self.create_individual(start, end, int_nodes, G)
            if not self._validate_path(G, child2, start, end, int_nodes):
                child2 = self.create_individual(start, end, int_nodes, G)

        return child1, child2
    
    def mutate(self, individual: List[int], G: nx.MultiDiGraph, probability: float = None,
               start_node: int = None, end_node: int = None,
               intermediate_nodes: List[int] = None,
               stagnation_count: int = 0) -> List[int]:
        """
        变异操作 - 自适应变异率 + 局部搜索

        Args:
            individual: 待变异的路径
            G: 路网图
            probability: 变异概率（如果为None则使用自适应）
            start_node: 起始节点（不可删除）
            end_node: 结束节点（不可删除）
            intermediate_nodes: 中间必经节点列表（不可删除）
            stagnation_count: 停滞代数（用于自适应变异率）
        """
        # 自适应变异率：停滞时增加变异
        if probability is None:
            base_mutation_rate = self.mutation_rate
            # 停滞时增加变异率（最多增加2倍）
            stagnation_factor = max(0.5, 1.0 + stagnation_count * 0.1)
            probability = min(0.8, base_mutation_rate * stagnation_factor)

        if np.random.random() > probability or len(individual) < 3:
            return individual.copy()

        mutated = individual.copy()

        # 根据停滞情况选择变异策略
        # 停滞严重时，使用更激进的变异（重新计算路径）
        # 停滞不严重时，使用局部搜索

        if stagnation_count > 10:
            # 严重停滞：直接重新计算路径
            try:
                if intermediate_nodes:
                    new_path = self.create_individual(start_node, end_node,
                                                      intermediate_nodes, G)
                    return new_path
            except:
                pass
        elif stagnation_count > 5:
            # 中度停滞：插入额外节点重新计算
            if len(mutated) > 3:
                random_pos = np.random.randint(1, len(mutated) - 1)
                random_node = mutated[random_pos]
                try:
                    if random_node != start_node and random_node != end_node:
                        new_path = self.create_individual(start_node, end_node,
                                                        intermediate_nodes + [random_node], G)
                        return new_path
                except:
                    pass
        else:
            # 轻度停滞或正常：局部搜索
            # 尝试替换中间节点为更优替代路径
            if len(mutated) > 4 and intermediate_nodes:
                # 找到可以变异的中间节点位置
                safe_positions = []
                for i in range(1, len(mutated) - 1):
                    if mutated[i] not in intermediate_nodes:
                        safe_positions.append(i)

                if safe_positions:
                    pos = np.random.choice(safe_positions)
                    old_node = mutated[pos]

                    # 尝试找到更好的替代路径段
                    try:
                        # 获取前一个节点和后一个节点
                        prev_node = mutated[pos - 1]
                        next_node = mutated[pos + 1]

                        # 找到 prev_node 和 next_node 的所有共同邻居
                        prev_neighbors = set(G.neighbors(prev_node))
                        next_neighbors = set(G.neighbors(next_node))
                        alt_nodes = prev_neighbors & next_neighbors

                        # 过滤掉已经在路径中的节点
                        alt_nodes = alt_nodes - set(mutated)

                        if alt_nodes:
                            # 选择一个替代节点
                            new_node = np.random.choice(list(alt_nodes))
                            # 构建新路径
                            new_path = mutated[:pos] + [new_node] + mutated[pos+1:]
                            # 验证路径有效性
                            int_nodes = intermediate_nodes if intermediate_nodes else []
                            if G is not None and self._validate_path(G, new_path, start_node, end_node, int_nodes):
                                return new_path
                    except:
                        pass

        # 如果局部搜索失败，重新计算路径
        try:
            if intermediate_nodes:
                new_path = self.create_individual(start_node, end_node,
                                                  intermediate_nodes, G)
                int_nodes = intermediate_nodes if intermediate_nodes else []
                if G is None or self._validate_path(G, new_path, start_node, end_node, int_nodes):
                    return new_path
        except:
            pass

        # 如果所有变异策略都失败，返回原路径的副本
        return individual.copy()
    
    def optimize(self, start_node: int, end_node: int, intermediate_nodes: List[int],
                G: nx.MultiDiGraph, congestion_scores: Dict, target_distance: Optional[float] = None,
                initial_path: List[int] = None, verbose: bool = True,
                node_coordinates: Optional[Dict[int, Tuple[float, float]]] = None) -> Tuple[List[int], Dict]:
        """
        执行遗传算法优化

        Args:
            start_node: 起始节点
            end_node: 结束节点
            intermediate_nodes: 中间节点
            G: 路网图
            congestion_scores: 拥堵系数
            target_distance: 目标距离
            initial_path: OR-Tools计算的初始优化路径（可选）
            verbose: 是否显示详细信息
            node_coordinates: 节点坐标字典，用于记录详细路径

        Returns:
            (最优路径, 优化历史)
        """
        # 重置路径记录状态
        self._last_recorded_path = None

        # 创建初始种群时传入 initial_path
        population = self.create_population(start_node, end_node, intermediate_nodes, G, initial_path)

        if verbose:
            print(f"开始遗传算法优化，种群大小: {len(population)}")
            if initial_path:
                print("已加载 OR-Tools 计算的初始路径作为种群种子")
            print(f"详细路径记录间隔: 每 {self.record_interval} 代")

        best_individual = None
        best_fitness = 0
        generation_history = []

        # 早停机制变量
        best_fitness_history = []
        stagnation_count = 0
        early_stop_generation = None

        for generation in range(self.generations):
            # 评估适应度
            fitness_scores = []
            for individual in population:
                fitness = self.evaluate_fitness(individual, G, congestion_scores, target_distance,
                                              start_node=start_node,
                                              end_node=end_node,
                                              intermediate_nodes=intermediate_nodes)
                fitness_scores.append(fitness)

                if fitness > best_fitness:
                    best_fitness = fitness
                    best_individual = individual.copy()

            # 计算平均适应度和距离
            avg_fitness = np.mean(fitness_scores)
            best_distance = self.calculate_route_distance(best_individual, G)

            # 记录历史
            history_entry = {
                'generation': generation,
                'best_fitness': best_fitness,
                'avg_fitness': avg_fitness,
                'best_distance': best_distance
            }

            # 按固定间隔记录详细路径信息（只有路径发生变化才记录）
            path_changed = self._is_path_changed(best_individual)
            should_record = (generation % self.record_interval == 0 or generation == self.generations - 1) and (path_changed)

            # 关键逻辑：到了固定间隔记录点时，只有路径发生变化才记录
            if should_record:
                # 使用通用函数计算路径统计
                route_stats = calculate_route_metrics(best_individual, G, congestion_scores)

                # 添加详细路径信息
                history_entry.update({
                    'detailed_path': {
                        'nodes': best_individual.copy(),
                        'node_count': len(best_individual),
                        'edge_count': route_stats['edge_count'],
                        'total_distance_m': round(best_distance, 2),
                        'total_distance_km': round(best_distance / 1000, 3),
                        'congestion_percentage': round(route_stats['congestion_percentage'], 2),
                        'avg_congestion_score': round(route_stats['avg_congestion_score'], 4)
                    }
                })

                # 如果提供了节点坐标，记录坐标信息
                if node_coordinates:
                    coords = []
                    for node in best_individual:
                        if node in node_coordinates:
                            lat, lon = node_coordinates[node]
                            coords.append({
                                'node_id': node,
                                'lat': round(lat, 6),
                                'lon': round(lon, 6)
                            })
                    history_entry['detailed_path']['coordinates'] = coords

                # 更新上一次记录的路径
                self._last_recorded_path = best_individual.copy()

            generation_history.append(history_entry)

            # 早停检测：连续10代适应度无明显改善
            best_fitness_history.append(best_fitness)
            if len(best_fitness_history) >= 10:
                recent_improvement = best_fitness_history[-1] - best_fitness_history[-10]
                if recent_improvement < 0.001:
                    stagnation_count += 1
                else:
                    stagnation_count = 0

                if stagnation_count >= 10 and generation >= 50:  # 至少50代后才允许早停
                    early_stop_generation = generation
                    if verbose:
                        print(f"早停：第 {generation} 代收敛（连续{10}代无明显改善）")
                    break

            if verbose and generation % 20 == 0:
                print(f"第 {generation} 代: 最佳适应度={best_fitness:.4f}, 平均适应度={avg_fitness:.4f}, 距离={best_distance/1000:.2f}km")

            # 选择父母（锦标赛选择 + 自适应选择压力）
            parents = self.select_parents(population, fitness_scores,
                                         generation=generation,
                                         total_generations=self.generations)

            # 生成新种群
            new_population = []

            # 保留精英
            elite_count = min(self.elite_size, len(population))
            elite_indices = np.argsort(fitness_scores)[-elite_count:]
            for idx in elite_indices:
                new_population.append(population[idx].copy())

            # 交叉和变异
            while len(new_population) < self.population_size:
                parent1, parent2 = parents[np.random.randint(0, len(parents))], \
                                  parents[np.random.randint(0, len(parents))]

                # 传递必要参数以保护途经点约束
                child1, child2 = self.crossover(parent1, parent2,
                                               start_node=start_node,
                                               end_node=end_node,
                                               intermediate_nodes=intermediate_nodes,
                                               G=G)
                # 使用自适应变异率
                child1 = self.mutate(child1, G,
                                    start_node=start_node,
                                    end_node=end_node,
                                    intermediate_nodes=intermediate_nodes,
                                    stagnation_count=stagnation_count)
                child2 = self.mutate(child2, G,
                                    start_node=start_node,
                                    end_node=end_node,
                                    intermediate_nodes=intermediate_nodes,
                                    stagnation_count=stagnation_count)

                new_population.extend([child1, child2])

            # 截断到指定大小
            population = new_population[:self.population_size]

        if verbose:
            print(f"遗传算法优化完成，最佳适应度: {best_fitness:.4f}")

        # 强制确保路径有效性（完整验证）
        int_nodes = intermediate_nodes if intermediate_nodes else []
        if best_individual is None or len(best_individual) < 2:
            best_individual = self.create_individual(start_node, end_node, int_nodes, G)
        else:
            # 使用完整路径验证
            if not self._validate_path(G, best_individual, start_node, end_node, int_nodes):
                print("警告: 最优路径无效，重新生成...")
                try:
                    best_individual = self.create_individual(start_node, end_node, int_nodes, G)
                except Exception:
                    # 兜底：强制修正首尾
                    if best_individual[0] != start_node:
                        if start_node in best_individual:
                            best_individual.remove(start_node)
                        best_individual.insert(0, start_node)
                    if best_individual[-1] != end_node:
                        if end_node in best_individual:
                            best_individual.remove(end_node)
                        best_individual.append(end_node)

        self.optimization_history = generation_history
        return best_individual, generation_history
    
    def calculate_route_distance(self, route: List[int], G: nx.MultiDiGraph) -> float:
        """计算路线总距离"""
        total_distance = 0
        for i in range(len(route) - 1):
            u, v = route[i], route[i + 1]
            try:
                edge_data = G[u][v][0]
                distance = edge_data.get('length', 0)
                total_distance += distance
            except:
                total_distance += 1000  # 惩罚距离
        return total_distance

class RoutePlanner:
    """智能路线规划器"""

    def __init__(self):
        """初始化路线规划器"""
        self.processor = OSMDataProcessor()
        self.net_processor = NetDataProcessor()  # Net路网处理器
        self.genetic_optimizer = GeneticOptimizer()
        self._net_mode = False  # 是否使用Net模式

    def parse_arguments(self):
        """解析命令行参数"""
        parser = argparse.ArgumentParser(description='智能路线规划器')
        parser.add_argument('--start', required=True, help='起始地址')
        parser.add_argument('--end', required=True, help='目标地址')
        parser.add_argument('--via', nargs='*', help='途经点地址列表')
        parser.add_argument('--distance', type=float, help='目标距离(公里)')
        parser.add_argument('--city', default='Beijing, China', help='城市名称')
        parser.add_argument('--visualize', action='store_true', help='生成可视化文件')
        parser.add_argument('--output', help='输出文件名')
        parser.add_argument('--generations', type=int, default=50, help='遗传算法迭代次数')
        parser.add_argument('--record-interval', type=int, default=50, help='详细路径记录间隔（每多少代记录一次）')
        parser.add_argument('--local-map', help='本地地图 XML 文件名 (data 目录下)')
        parser.add_argument('--data-dir', default='data', help='本地数据目录路径')

        return parser.parse_args()
    
    def load_network(self, location: str, start_lat: float = None, start_lon: float = None,
                     end_lat: float = None, end_lon: float = None,
                     intermediate_coords: List[Tuple[float, float]] = None,
                     margin_km: float = 1.0,
                     local_xml_file: str = None,
                     data_dir: str = "data",
                     net_file: str = None) -> nx.MultiDiGraph:
        """
        加载路网数据

        Args:
            location: 位置名称（用于错误信息）
            start_lat: 起始纬度
            start_lon: 起始经度
            end_lat: 终止纬度
            end_lon: 终止经度
            intermediate_coords: 途经点坐标列表
            margin_km: 边界扩展距离（公里）
            local_xml_file: 本地 OSM XML 文件名（可选）
            data_dir: 本地数据目录路径
            net_file: Net路网文件路径（.net.xml可选）

        Returns:
            路网图

        Note:
            优先使用 net_file，如果提供则使用 Net 模式
        """
        # Net模式优先
        if net_file:
            print(f"使用Net路网模式: {net_file}")
            self._net_mode = True
            G = self.net_processor.load_network_from_net(net_file)
            return G

        self._net_mode = False

        # 只支持本地 XML 文件模式
        if not local_xml_file:
            raise ValueError(
                "离线模式需要指定本地 XML 文件\n"
                "请使用 --local-map 参数指定 XML 文件名\n"
                "例如: --local-map THU-PKU.osm"
            )

        print(f"正在使用本地地图文件: {local_xml_file}")
        print(f"数据目录: {data_dir}")

        # 如果有坐标信息，使用边界框方式（更精确）
        if all(x is not None for x in [start_lat, start_lon, end_lat, end_lon]):
            print(f"正在从本地 XML 文件加载路网 (扩展距离: {margin_km}km)...")
            G = self.processor.get_drive_network_from_local_bounds(
                local_xml_file,
                start_lat, start_lon,
                end_lat, end_lon,
                intermediate_coords,
                data_dir=data_dir,
                margin_km=margin_km
            )
        else:
            # 没有坐标信息，加载完整路网
            print(f"正在从本地 XML 文件加载完整路网...")
            G = self.processor.get_drive_network_from_local(
                local_xml_file,
                data_dir=data_dir
            )

        # 添加距离权重
        for u, v, data in G.edges(data=True):
            if 'length' not in data:
                data['length'] = 100  # 默认距离

        return G
    
    def solve_with_ortools(self, start_node: int, end_node: int,
                          intermediate_nodes: List[int], G: nx.MultiDiGraph) -> List[int]:
        """
        使用OR-Tools求解初始路径
        采用骨架求解方法：只处理关键节点，确定访问顺序

        Args:
            start_node: 起始节点
            end_node: 结束节点
            intermediate_nodes: 中间节点
            G: 路网图

        Returns:
            路径节点列表
        """
        print("使用OR-Tools求解初始路径...")

        # 关键点列表：起点 + 途经点 + 终点
        # 索引: 0=起点, 1..n-2=途经点, n-1=终点
        node_list = [start_node] + intermediate_nodes + [end_node]
        num_nodes = len(node_list)

        print(f"关键点数量: {num_nodes}")
        print(f"  起点 (索引0): {self.processor._get_node_name(G, start_node)}")
        for i, node in enumerate(intermediate_nodes):
            print(f"  途经点 (索引{i+1}): {self.processor._get_node_name(G, node)}")
        print(f"  终点 (索引{num_nodes-1}): {self.processor._get_node_name(G, end_node)}")

        # 如果没有途经点，直接使用最短路径
        if len(intermediate_nodes) == 0:
            print("无途经点，直接计算起点到终点的最短路径...")
            try:
                full_route = nx.shortest_path(G, start_node, end_node, weight='length')
                print(f"最短路径包含 {len(full_route)} 个节点")
                return full_route
            except nx.NetworkXNoPath:
                raise RuntimeError(f"无法找到从起点到终点的路径")

        print(f"正在计算 {num_nodes}x{num_nodes} 距离矩阵...")

        # 预计算关键点之间的距离矩阵（使用Dijkstra）
        distance_matrix = []
        for from_node in node_list:
            row = []
            lengths = nx.single_source_dijkstra_path_length(G, from_node, weight='length')
            for to_node in node_list:
                if to_node in lengths:
                    row.append(int(lengths[to_node]))
                else:
                    row.append(10000000)  # 无穷大（不可达）
            distance_matrix.append(row)

        # 打印距离矩阵用于调试
        print("距离矩阵 (米):")
        for i, row in enumerate(distance_matrix):
            node_name = self.processor._get_node_name(G, node_list[i])[:20]
            print(f"  {i}: {node_name}: {row}")

        # 使用 RoutingIndexManager 处理非闭环TSP（start != end）
        # 重要: [0] 表示车辆从索引0(起点)出发, [num_nodes-1] 表示车辆到索引n-1(终点)结束
        manager = pywrapcp.RoutingIndexManager(
            num_nodes,      # 节点数量
            1,              # 车辆数量
            [0],            # 起点索引列表（每辆车的起点）
            [num_nodes - 1] # 终点索引列表（每辆车的终点）
        )
        routing = pywrapcp.RoutingModel(manager)

        # 距离回调函数
        def distance_callback(from_index, to_index):
            from_node_idx = manager.IndexToNode(from_index)
            to_node_idx = manager.IndexToNode(to_index)
            return distance_matrix[from_node_idx][to_node_idx]

        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        # 添加距离维度约束，确保所有节点都被访问
        # 这是关键：通过添加维度，OR-Tools会确保所有节点都在路径中
        routing.AddDimension(
            transit_callback_index,
            0,           # 无等待时间
            100000000,   # 最大总距离
            True,        # 从零开始累积
            'Distance'
        )

        # 确保所有中间节点必须被访问（不允许跳过）
        # 通过不添加Disjunction，OR-Tools默认会访问所有节点
        # 但为了更明确，我们可以检查并强制约束
        print(f"设置约束: 所有 {len(intermediate_nodes)} 个途经点必须被访问")

        # 求解参数
        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC)
        # 增加搜索时间限制
        search_parameters.time_limit.seconds = 30

        solution = routing.SolveWithParameters(search_parameters)

        if not solution:
            print("OR-Tools求解失败，尝试使用贪心方法...")
            # 备选方案：按顺序连接所有节点
            skeleton_route = node_list.copy()
        else:
            # 提取骨架路径（关键点的访问顺序）
            print("提取骨架路径...")
            skeleton_route = []
            index = routing.Start(0)  # 从车辆0的起点开始

            visited_count = 0
            while not routing.IsEnd(index):
                node_idx = manager.IndexToNode(index)
                skeleton_route.append(node_list[node_idx])
                index = solution.Value(routing.NextVar(index))
                visited_count += 1
                if visited_count > num_nodes + 1:  # 防止无限循环
                    print("警告: 检测到循环，强制退出")
                    break

            # 添加终点
            end_node_idx = manager.IndexToNode(index)
            skeleton_route.append(node_list[end_node_idx])

        # 验证骨架路径
        print(f"骨架路径节点数: {len(skeleton_route)}")
        print(f"骨架路径: {[self.processor._get_node_name(G, n) for n in skeleton_route]}")

        # 检查是否所有中间节点都在骨架路径中
        missing_nodes = []
        for node in intermediate_nodes:
            if node not in skeleton_route:
                missing_nodes.append(node)
                print(f"警告: 途经点 {self.processor._get_node_name(G, node)} 未在骨架路径中!")

        # 如果有缺失的节点，手动插入
        if missing_nodes:
            print(f"正在插入 {len(missing_nodes)} 个缺失的途经点...")
            for missing_node in missing_nodes:
                # 找到最佳插入位置（使总距离最小）
                best_pos = 1  # 默认插入到起点之后
                best_cost = float('inf')

                for pos in range(1, len(skeleton_route)):
                    prev_node = skeleton_route[pos - 1]
                    next_node = skeleton_route[pos]

                    # 计算插入成本
                    try:
                        cost_before = nx.shortest_path_length(G, prev_node, next_node, weight='length')
                        cost_after = (nx.shortest_path_length(G, prev_node, missing_node, weight='length') +
                                     nx.shortest_path_length(G, missing_node, next_node, weight='length'))
                        insert_cost = cost_after - cost_before

                        if insert_cost < best_cost:
                            best_cost = insert_cost
                            best_pos = pos
                    except nx.NetworkXNoPath:
                        continue

                skeleton_route.insert(best_pos, missing_node)
                print(f"  插入 {self.processor._get_node_name(G, missing_node)} 到位置 {best_pos}")

        # 验证起点和终点
        if skeleton_route[0] != start_node:
            print(f"警告: 骨架路径起点 {skeleton_route[0]} 与指定起点 {start_node} 不一致，修正中...")
            if start_node in skeleton_route:
                skeleton_route.remove(start_node)
            skeleton_route.insert(0, start_node)
        if skeleton_route[-1] != end_node:
            print(f"警告: 骨架路径终点 {skeleton_route[-1]} 与指定终点 {end_node} 不一致，修正中...")
            if end_node in skeleton_route[:-1]:
                skeleton_route.remove(end_node)
            skeleton_route.append(end_node)

        print(f"最终骨架路径: {[self.processor._get_node_name(G, n) for n in skeleton_route]}")

        # 展开骨架路径为完整路径（连接相邻关键点）
        print("展开为完整路径...")
        full_route = []
        for i in range(len(skeleton_route) - 1):
            try:
                segment_path = nx.shortest_path(G, skeleton_route[i], skeleton_route[i+1], weight='length')
                # 避免重复添加连接点
                if i > 0:
                    segment_path = segment_path[1:]
                full_route.extend(segment_path)
            except nx.NetworkXNoPath:
                print(f"警告: 无法从节点 {skeleton_route[i]} 找到到节点 {skeleton_route[i+1]} 的路径")
                full_route.append(skeleton_route[i+1])

        # 最终验证
        if full_route[0] != start_node or full_route[-1] != end_node:
            print(f"错误: 完整路径端点不正确!")
            print(f"  期望起点: {start_node}, 实际: {full_route[0]}")
            print(f"  期望终点: {end_node}, 实际: {full_route[-1]}")

        # 验证所有途经点都在完整路径中
        for node in intermediate_nodes:
            if node not in full_route:
                print(f"错误: 途经点 {self.processor._get_node_name(G, node)} 不在最终路径中!")

        print(f"OR-Tools求解成功，初始路径包含 {len(full_route)} 个节点")
        print(f"  路径起点: {self.processor._get_node_name(G, full_route[0])}")
        print(f"  路径终点: {self.processor._get_node_name(G, full_route[-1])}")
        return full_route

    def plan_route(self, args) -> Dict:
        """
        执行完整的路线规划流程
        
        Args:
            args: 命令行参数
            
        Returns:
            规划结果
        """
        print("=" * 50)
        print("智能路线规划")
        print("=" * 50)
        
        # 1. 获取地址坐标（优先使用显式经纬度，避免地理编码偏移）
        print("\\n1. 地址解析...")
        if getattr(args, 'start_lat', None) is not None and getattr(args, 'start_lon', None) is not None:
            start_lat, start_lon = float(args.start_lat), float(args.start_lon)
            end_lat, end_lon = float(args.end_lat), float(args.end_lon)
            intermediate_coords = []
            lats = getattr(args, 'intermediate_lats', []) or []
            lons = getattr(args, 'intermediate_lons', []) or []
            if len(lats) != len(lons):
                raise ValueError("intermediate_lats 与 intermediate_lons 长度不一致")
            for lat, lon in zip(lats, lons):
                intermediate_coords.append((float(lat), float(lon)))
            print(f"起始点: ({start_lat:.6f}, {start_lon:.6f})")
            print(f"目标点: ({end_lat:.6f}, {end_lon:.6f})")
            if intermediate_coords:
                print(f"途经点: {intermediate_coords}")
        else:
            start_lat, start_lon = self.processor.get_location_data(args.start)
            end_lat, end_lon = self.processor.get_location_data(args.end)
            
            print(f"起始点: {args.start} ({start_lat:.6f}, {start_lon:.6f})")
            print(f"目标点: {args.end} ({end_lat:.6f}, {end_lon:.6f})")
            
            intermediate_coords = []
            if args.via:
                print(f"途经点: {args.via}")
                for via_point in args.via:
                    via_lat, via_lon = self.processor.get_location_data(via_point)
                    intermediate_coords.append((via_lat, via_lon))
        
        # 2. 加载路网数据
        print("\\n2. 加载路网数据...")
        net_file = getattr(args, 'net_file', None)
        G = self.load_network(
            args.city,
            start_lat=start_lat,
            start_lon=start_lon,
            end_lat=end_lat,
            end_lon=end_lon,
            intermediate_coords=intermediate_coords,
            margin_km=getattr(args, 'margin_km', 1.0),  # 默认扩展1公里
            local_xml_file=getattr(args, 'local_map', None),
            data_dir=getattr(args, 'data_dir', 'data'),
            net_file=net_file
        )

        # 3. 找到对应的节点
        if self._net_mode:
            # Net模式：使用net_processor的坐标转换
            start_node = self.net_processor.find_nearest_node(G, start_lat, start_lon)
            end_node = self.net_processor.find_nearest_node(G, end_lat, end_lon)

            intermediate_nodes = []
            for lat, lon in intermediate_coords:
                node = self.net_processor.find_nearest_node(G, lat, lon)
                intermediate_nodes.append(node)
        else:
            # OSM模式：使用processor的坐标匹配
            start_node = self.processor.get_node_by_coordinates(G, start_lat, start_lon)
            end_node = self.processor.get_node_by_coordinates(G, end_lat, end_lon)

            intermediate_nodes = []
            for lat, lon in intermediate_coords:
                node = self.processor.get_node_by_coordinates(G, lat, lon)
                intermediate_nodes.append(node)
        
        print(f"起始节点: {start_node}")
        print(f"目标节点: {end_node}")
        print(f"途经节点: {intermediate_nodes}")
        
        # 4. 计算拥堵系数
        # 该函数可以定制，从而实现不限于拥堵系数的其他权重计算
        print("\\n3. 计算拥堵系数...")
        congestion_scores = self.processor.calculate_congestion_score(G)

        # 5. 使用OR-Tools求解初始解
        print("\\n4. 使用OR-Tools求解初始解...")
        initial_route = self.solve_with_ortools(start_node, end_node, intermediate_nodes, G)

        # 6. 使用遗传算法优化
        print("\\n5. 使用遗传算法优化...")
        target_distance = args.distance * 1000 if args.distance else None

        # 设置遗传算法参数
        self.genetic_optimizer.generations = args.generations
        self.genetic_optimizer.record_interval = args.record_interval

        # 提取节点坐标用于详细路径记录
        node_coordinates = {}
        for node_id in G.nodes():
            node_data = G.nodes[node_id]
            if 'x' in node_data and 'y' in node_data:
                node_coordinates[node_id] = (node_data['y'], node_data['x'])  # (lat, lon)

        # 关键修改：传入 initial_route，让GA基于OR-Tools的最优顺序进行优化
        optimized_route, optimization_history = self.genetic_optimizer.optimize(
            start_node, end_node, intermediate_nodes, G, congestion_scores, target_distance,
            initial_path=initial_route,  # 将OR-Tools的结果传给遗传算法
            node_coordinates=node_coordinates  # 传递节点坐标用于记录详细路径
        )

        # 7. 计算最终统计信息
        print("\\n6. 计算路线统计...")
        final_stats = self.processor.calculate_route_stats(G, optimized_route, congestion_scores)

        # 8. 格式化结果
        route_data = {
            'nodes': optimized_route,
            'edge_count': final_stats['edge_count'],
            'total_distance': final_stats['total_distance'],
            'total_distance_km': final_stats['total_distance'] / 1000,
            'congestion_percentage': final_stats['congestion_percentage'],
            'avg_congestion_score': final_stats['avg_congestion_score']
        }

        # 如果是Net模式，添加edge_id序列
        if self._net_mode:
            # 重新加载网络以获取edge_id信息
            edge_id_to_info = {}
            try:
                import xml.etree.ElementTree as ET
                tree = ET.parse(net_file)
                root = tree.getroot()
                for edge_elem in root.findall('.//edge'):
                    edge_id = edge_elem.get('id')
                    function = edge_elem.get('function', '')
                    if function == 'internal' or edge_id.startswith(':'):
                        continue
                    from_node = edge_elem.get('from')
                    to_node = edge_elem.get('to')
                    edge_id_to_info[edge_id] = {'from': from_node, 'to': to_node}

                # 转换为edge_id序列（使用net_processor）
                edge_ids, _ = self.net_processor.nodes_to_edge_ids(G, optimized_route, edge_id_to_info)
                route_data['edge_ids'] = edge_ids
                print(f"  路径包含 {len(edge_ids)} 条边")
            except Exception as e:
                print(f"  警告: 无法生成edge_id序列: {e}")

        result = {
            'input': {
                'start_location': args.start if hasattr(args, 'start') else None,
                'end_location': args.end if hasattr(args, 'end') else None,
                'intermediate_locations': args.via if hasattr(args, 'via') else [],
                'target_distance': args.distance,
                'city': args.city,
                'margin_km': getattr(args, 'margin_km', 1.0),
                'local_xml_file': getattr(args, 'local_map', None),
                'data_dir': getattr(args, 'data_dir', 'data'),
                'net_file': net_file,
                'start_lat': start_lat,
                'start_lon': start_lon,
                'end_lat': end_lat,
                'end_lon': end_lon,
                'intermediate_lats': [coord[0] for coord in intermediate_coords] if intermediate_coords else [],
                'intermediate_lons': [coord[1] for coord in intermediate_coords] if intermediate_coords else []
            },
            'route': route_data,
            'optimization': {
                'algorithm': 'OR-Tools + Genetic Algorithm',
                'generations': args.generations,
                'final_fitness': optimization_history[-1]['best_fitness'] if optimization_history else 0,
                'history': optimization_history
            },
            'details': final_stats['edge_details'],
            'graph_type': 'net' if self._net_mode else 'osm',
            'timestamp': datetime.now().isoformat()
        }
        
        return result
    
    def save_results(self, result: Dict, filename: str):
        """保存结果到文件"""
        if filename:
            # JSON 默认不能序列化 numpy 类型（如 int64/float64/ndarray），提供转换器
            def _json_converter(obj):
                try:
                    import numpy as _np
                except Exception:
                    _np = None

                if _np is not None:
                    if isinstance(obj, (_np.integer,)):
                        return int(obj)
                    if isinstance(obj, (_np.floating,)):
                        return float(obj)
                    if isinstance(obj, (_np.ndarray,)):
                        return obj.tolist()

                # Fallback
                return str(obj)

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=_json_converter)
            print(f"结果已保存到: {filename}")
        else:
            # 打印到控制台
            print("\\n" + "=" * 50)
            print("路线规划结果")
            print("=" * 50)
            def _json_converter_console(obj):
                try:
                    import numpy as _np
                except Exception:
                    _np = None

                if _np is not None:
                    if isinstance(obj, (_np.integer,)):
                        return int(obj)
                    if isinstance(obj, (_np.floating,)):
                        return float(obj)
                    if isinstance(obj, (_np.ndarray,)):
                        return obj.tolist()
                return str(obj)

            print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_converter_console))
    
    def generate_summary_report(self, result: Dict) -> str:
        """生成总结报告"""
        route = result['route']
        opt = result['optimization']

        report = f"""
                路线规划结果总结报告
                ========================

                输入参数:
                - 起始点: {result['input']['start_location']}
                - 目标点: {result['input']['end_location']}
                - 途经点: {', '.join(result['input']['intermediate_locations']) if result['input']['intermediate_locations'] else '无'}
                - 目标距离: {result['input']['target_distance']}公里 (如设定)
                - 城市: {result['input']['city']}

                路线统计:
                - 总距离: {route['total_distance_km']:.2f} 公里
                - 路段数量: {route['edge_count']} 条
                - 路线节点: {len(route['nodes'])} 个
                - 平均拥堵系数: {route['avg_congestion_score']:.3f} (越低越畅通)
                - 拥堵路段占比: {route['congestion_percentage']:.1f}%

                优化信息:
                - 优化算法: {opt['algorithm']}
                - 进化代数: {opt['generations']}
                - 最终适应度: {opt['final_fitness']:.4f}
                - 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

                约束满足情况:
                1. 途经点约束: {'✓ 已满足' if result['input']['intermediate_locations'] else '无约束'}
                2. 路径长度约束: {'✓ 接近目标' if result['input']['target_distance'] and abs(route['total_distance_km'] - result['input']['target_distance']) < 1.0 else '无目标距离' if not result['input']['target_distance'] else '✓ 可接受范围内'}
                3. 拥堵优化: {'✓ 已优化' if route['congestion_percentage'] < 40 else '✓ 已优化'}
                """
        return report

def main():
    """主函数"""
    # 设置matplotlib环境
    setup_matplotlib_for_plotting()
    
    # 创建路线规划器
    planner = RoutePlanner()
    
    # 解析参数
    args = planner.parse_arguments()
    
    try:
        # 执行路线规划
        result = planner.plan_route(args)
        
        # 保存结果
        if args.output:
            planner.save_results(result, args.output)
        else:
            # 直接显示
            planner.save_results(result, None)
        
        # 生成并显示总结报告
        summary = planner.generate_summary_report(result)
        print(summary)
        
        print("\\n路线规划完成!")
        return 0
        
    except KeyboardInterrupt:
        print("\\n用户中断操作")
        return 1
    except Exception as e:
        print(f"\\n错误: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())