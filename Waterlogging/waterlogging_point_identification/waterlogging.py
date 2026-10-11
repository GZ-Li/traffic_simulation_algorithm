# cd /home/nkk/work/src/python/Waterlogging/waterlogging_point_identification && /usr/bin/python3 waterlogging.py
import pandas as pd
import numpy as np
import os


class WaterloggingRiskAnalyzer:
    """
    内涝风险分析器 (生产版)
    输入: raw_data.xlsx
    输出: 包含完整属性的高风险内涝点
    """

    def __init__(self, file_path=None):
        # 如果未指定文件路径，使用脚本同目录下的 raw_data_V2.xlsx
        if file_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            file_path = os.path.join(script_dir, "raw_data_V2.xlsx")
        self.file_path = file_path
        self.data = None

        # --- AHP 判断矩阵 (保持不变) ---
        # 顺序: [低洼程度, 不透水率, 历史发生概率]
        self.comparison_matrix = np.array([
            [1, 2, 1 / 3],
            [1 / 2, 1, 1 / 5],
            [3, 5, 1]
        ])

        # --- 列名映射配置 ---
        # 作用：将Excel中文表头映射为代码内部使用的英文变量名
        # 注意：这里不仅映射计算列，也映射了'序号'和'类型'以便统一管理，
        # 如果你想保留中文列名输出，可以修改最后输出时的 rename 逻辑。
        self.col_mapping = {
            '序号': 'ID',
            '经度': 'Longitude',
            '纬度': 'Latitude',
            '历史发生概率': 'History_Prob',
            '低洼程度': 'Depression_Degree',
            '不透水率': 'Impermeability'
        }

    def load_data(self):
        """
        读取并清洗数据，严格检查文件是否存在
        """
        if not os.path.exists(self.file_path):
            # 这里的报错是必须的，生产环境下没有数据就应该停止运行
            raise FileNotFoundError(f"错误: 未在项目目录下找到文件 '{self.file_path}'。请确保文件位置正确。")

        try:
            print(f">> [System] 正在读取: {self.file_path} ...")
            df = pd.read_excel(self.file_path)

            # 1. 映射列名
            # 检查Excel列名是否包含所有必要的key
            missing_cols = [k for k in self.col_mapping.keys() if k not in df.columns]
            if missing_cols:
                raise ValueError(f"Excel表头缺失以下列: {missing_cols}")

            df.rename(columns=self.col_mapping, inplace=True)

            # 2. 数据清洗 (只剔除计算所需列为空的行，保留其他信息)
            calc_cols = ['History_Prob', 'Depression_Degree', 'Impermeability']
            df_clean = df.dropna(subset=calc_cols).copy()

            self.data = df_clean
            print(f">> [System] 数据加载成功: 共 {len(self.data)} 条记录")
            return self.data

        except Exception as e:
            raise RuntimeError(f"数据读取过程中发生错误: {e}")

    def _calculate_ahp_weights(self):
        """
        AHP 权重计算核心算法 (保持学术严谨性)
        """
        matrix = self.comparison_matrix
        n = matrix.shape[0]

        # 列向量归一化 & 求行均值
        col_sum = matrix.sum(axis=0)
        matrix_norm = matrix / col_sum
        weights = matrix_norm.sum(axis=1) / n

        # 一致性检验 (CR)
        Aw = np.dot(matrix, weights)
        lambda_max = np.mean(Aw / weights)
        CI = (lambda_max - n) / (n - 1)
        RI = {3: 0.58}.get(n, 0.58)
        CR = CI / RI

        print(f">> [AHP] 权重计算: {weights} (CR={CR:.4f})")
        if CR >= 0.1:
            print("   [警告] 判断矩阵一致性较差 (CR > 0.1)")

        return weights

    def run_analysis(self):
        """
        执行分析并返回完整结果
        """
        if self.data is None:
            self.load_data()

        df = self.data.copy()
        weights = self._calculate_ahp_weights()

        # 1. 归一化 (仅用于计算，生成临时列，不覆盖原始数据)
        # 这样可以保证最后输出时，原始的'低洼程度'等绝对值依然存在
        target_cols = ['Depression_Degree', 'Impermeability', 'History_Prob']
        for col in target_cols:
            min_val = df[col].min()
            max_val = df[col].max()
            if max_val - min_val != 0:
                df[f'{col}_norm'] = (df[col] - min_val) / (max_val - min_val)
            else:
                df[f'{col}_norm'] = 0

        # 2. 计算得分 (Score)
        # 权重对应: 0:低洼, 1:不透水, 2:历史
        df['Risk_Score'] = (
                                   df['Depression_Degree_norm'] * weights[0] +
                                   df['Impermeability_norm'] * weights[1] +
                                   df['History_Prob_norm'] * weights[2]
                           ) * 100

        # 3. 筛选高风险点 (Score >= 85)
        high_risk_df = df[df['Risk_Score'] >= 85].sort_values(by='Risk_Score', ascending=False)

        # 4. 清理临时计算列 (norm列)，只保留原始属性和最终得分
        # 找出所有以 '_norm' 结尾的列并删除
        cols_to_drop = [c for c in high_risk_df.columns if c.endswith('_norm')]
        final_output = high_risk_df.drop(columns=cols_to_drop)

        return final_output


if __name__ == "__main__":
    # 直接使用脚本同目录下的 raw_data_V2.xlsx，无需手动指定
    analyzer = WaterloggingRiskAnalyzer()

    try:
        # 运行分析
        results = analyzer.run_analysis()

        print("\n" + "=" * 40)
        print(f"最终筛选结果 (High Risk Points)")
        print("=" * 40)

        if not results.empty:
            # 设置pandas显示选项，防止列被折叠
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 1000)

            # 输出前5个最高风险点详情
            print(results.head(10))

            # 可选：将结果保存为新的 Excel
            output_file = "water_high_risk_points.xlsx"
            results.to_excel(output_file, index=False)
            print(f"\n>> 完整筛选结果已保存至: {output_file}")

    except FileNotFoundError as e:
        print(e)
    except Exception as e:
        print(f"发生未知错误: {e}")
