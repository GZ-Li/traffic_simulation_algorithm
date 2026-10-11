# 预处理地图，删除其中的黄灯相位和全红相位，使之符合信控算法的需求

import argparse
from mosstool.type import Map
from mosstool.map.builder import Builder
from mosstool.util.format_converter import dict2pb


def get_args():
    parser = argparse.ArgumentParser(
        description="用于预处理地图，删除其中的黄灯相位和全红相位，使之符合信控算法的需求"
    )
    parser.add_argument("--map_file", type=str)
    parser.add_argument("--output_file", type=str)
    return parser.parse_args()


def main():
    args = get_args()

    with open(args.map_file, "rb") as f:
        pb = f.read()

    m = Map()
    m.ParseFromString(pb)

    builder = Builder(
        net=m,
        proj_str=m.header.projection,
        traffic_light_mode="green_red",
    )

    m_dict = builder.build("tl_algorithm")
    m = dict2pb(m_dict, Map())

    with open(args.output_file, "wb") as f:
        f.write(m.SerializeToString())


if __name__ == "__main__":
    main()
