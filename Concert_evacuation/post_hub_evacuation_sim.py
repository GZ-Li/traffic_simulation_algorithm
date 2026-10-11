#!/usr/bin/env python3
"""服务器端单车 OD 撤离模拟脚本。

功能：
1. 连接模拟器 engine。
2. 从 MongoDB 读取一个 person 模板。
3. 根据用户输入的 origin_road / destination_road 查询对应 lane_id。
4. 调用 AddPerson 在模拟器中新增一辆自动寻路车辆。
5. 推进模拟器并跟踪这辆车，输出从 O 到 D 的耗时。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# 如果本脚本放在原服务器脚本同一层目录，通常上级目录里会有 trafficlight_rl。
# 例如：
#   project_root/
#     trafficlight_rl/
#     scripts/post_hub_evacuation_sim.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# 这是原 smoke_test 脚本里的默认 Mongo 连接串。
# 更推荐在服务器终端用 MONGO_URI 环境变量覆盖它，避免把账号密码长期写在脚本里。
DEFAULT_MONGO_URI = "mongodb://root:qZ7ILv0Xs6VnJcPg12KU3AoYhWKb4Cla@172.16.40.166:27017"


@dataclass
class SingleVehicleResult:
    """保存单车 OD 的运行结果，最后会转成 JSON 输出。"""

    origin_road: str
    destination_road: str
    origin_lane_candidates: List[int]
    destination_lane_candidates: List[int]
    origin_lane_id: Optional[int]
    destination_lane_id: Optional[int]
    person_id: Optional[int]
    departure_time: Optional[int]
    started_time: Optional[int]
    arrived_time: Optional[int]
    travel_time_seconds: Optional[int]
    status: str
    trace: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "origin_road": self.origin_road,
            "destination_road": self.destination_road,
            "origin_lane_candidates": self.origin_lane_candidates,
            "destination_lane_candidates": self.destination_lane_candidates,
            "origin_lane_id": self.origin_lane_id,
            "destination_lane_id": self.destination_lane_id,
            "person_id": self.person_id,
            "departure_time": self.departure_time,
            "started_time": self.started_time,
            "arrived_time": self.arrived_time,
            "travel_time_seconds": self.travel_time_seconds,
            "status": self.status,
            "trace": self.trace,
        }


class Env:
    """对模拟器 engine 做一层很薄的封装。"""

    def __init__(
        self,
        mongo_uri: str,
        map_db: str,
        map_coll: str,
        agent_db: str,
        agent_coll: str,
        start_step: int,
        step_size: int,
        step_count: int,
    ) -> None:
        from trafficlight_rl.engine import get_engine

        # get_engine 会连接/启动模拟器。
        # map_db/map_coll 指地图数据；agent_db/agent_coll 指初始 person/vehicle 数据。
        self.eng = get_engine(
            mongo_uri=mongo_uri,
            map_db=map_db,
            map_coll=map_coll,
            agent_db=agent_db,
            agent_coll=agent_coll,
            start_step=start_step,
            total_step=(step_count + 1) * step_size,
            use_max_pressure=True,
            output_sql_dsn="",
            output_job_prefix="post_hub_evacuation_sim_",
            output_bbox=None,
        )
        self.step_size = step_size

    def step_once(self) -> None:
        # 让模拟器前进一步。默认 step_size=30，即每步 30 秒。
        self.eng.next_step(self.step_size)

    def stop(self) -> None:
        # 脚本结束时停止模拟器，避免服务器上残留进程。
        self.eng._stop_simulator()


def normalize_person_for_add(person: Dict[str, Any]) -> Dict[str, Any]:
    """把 Mongo 里的 person 模板整理成 AddPerson 接口能接受的字段。"""

    # 有些模板字段是 snake_case，AddPerson 接口需要 camelCase。
    field_map = {
        "vehicle_attribute": "vehicleAttribute",
        "bus_attribute": "busAttribute",
        "pedestrian_attribute": "pedestrianAttribute",
        "bike_attribute": "bikeAttribute",
        "output_when_sleep": "outputWhenSleep",
    }

    normalized: Dict[str, Any] = {}
    for key, value in person.items():
        normalized[field_map.get(key, key)] = value

    # 新增 person 时不能复用模板里已有的 id/base，避免和原数据冲突。
    normalized.pop("id", None)
    normalized.pop("base", None)

    # 只保留 AddPerson 需要/可能接受的字段。
    allowed = {
        "attribute",
        "home",
        "schedules",
        "vehicleAttribute",
        "busAttribute",
        "pedestrianAttribute",
        "bikeAttribute",
        "labels",
        "profile",
        "work",
        "outputWhenSleep",
        "type",
    }
    return {key: value for key, value in normalized.items() if key in allowed}


def load_person_template_from_mongo(mongo_uri: str, agent_db: str, agent_coll: str) -> Dict[str, Any]:
    """从 MongoDB 读取一个已有 person，作为 AddPerson 的模板。"""

    import pymongo

    client = pymongo.MongoClient(mongo_uri)
    try:
        # 原 smoke_test 的逻辑是取集合中的第一条文档，并使用其中 data 字段。
        doc = client[agent_db][agent_coll].find_one({}, {"_id": 0})
        if not doc or not isinstance(doc.get("data"), dict):
            raise RuntimeError("Cannot find valid person template in Mongo collection")

        template = normalize_person_for_add(copy.deepcopy(doc["data"]))
        if not template:
            raise RuntimeError("Template becomes empty after normalization")
        return template
    finally:
        client.close()


async def resolve_start_end_lanes(
    env: Env,
    origin_road: str,
    destination_road: str,
) -> Tuple[int, int, List[int], List[int]]:
    """把 road_id 转换成 AddPerson 需要的 lane_id。"""

    # AddPerson 的出发点/终点需要 lane_position，因此不能直接传 road_id。
    origin_lanes = await env.eng.get_lane_id_of_road(origin_road)
    destination_lanes = await env.eng.get_lane_id_of_road(destination_road)
    if not origin_lanes or not destination_lanes:
        raise RuntimeError(f"Cannot resolve lane ids for OD: {origin_road} -> {destination_road}")
    origin_lane_candidates = [int(lane_id) for lane_id in origin_lanes]
    destination_lane_candidates = [int(lane_id) for lane_id in destination_lanes]
    return (
        origin_lane_candidates[0],
        destination_lane_candidates[0],
        origin_lane_candidates,
        destination_lane_candidates,
    )


def build_person_auto_route(
    template: Dict[str, Any],
    origin_lane_id: int,
    destination_lane_id: int,
    departure_time: int,
) -> Dict[str, Any]:
    """构造 AddPerson 的 person payload。

    关键字段：
    - home.lane_position：车辆出发位置。
    - schedules[0].departure_time：车辆计划出发的模拟器绝对时间。
    - trips[0].end.lane_position：车辆目的地位置。
    - routes=[]：不手写路线，让模拟器自动寻路。
    """

    person = copy.deepcopy(template)
    person["home"] = {
        "lane_position": {
            "lane_id": int(origin_lane_id),
            "s": 1.0,
        }
    }
    person["schedules"] = [
        {
            "departure_time": int(departure_time),
            "loop_count": 1,
            "trips": [
                {
                    "mode": "TRIP_MODE_DRIVE_ONLY",
                    "end": {
                        "lane_position": {
                            "lane_id": int(destination_lane_id),
                            "s": 1.0,
                        }
                    },
                    "routes": [],
                }
            ],
        }
    ]
    return person


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add one custom OD vehicle to the simulator and output travel time."
    )

    # 单车 OD 参数：你每次只需要改这两个 road id。
    parser.add_argument("--origin_road", required=True, help="起点 road id，例如 200000480")
    parser.add_argument("--destination_road", required=True, help="终点 road id，例如 200000160")

    # Mongo / 地图 / 人员集合参数。默认值沿用原 smoke_test 脚本。
    parser.add_argument("--mongo_uri", default=os.environ.get("MONGO_URI", DEFAULT_MONGO_URI))
    parser.add_argument("--map_db", default="tsingroc")
    parser.add_argument("--map_coll", default="map_pku_wuhan_demo_1015c_rl")
    parser.add_argument("--agent_db", default="tsingroc")
    parser.add_argument("--agent_coll", default="person_test_20250724_1015cc")

    # 模拟器时间参数。
    parser.add_argument("--start", type=int, default=0, help="模拟器起始 step")
    parser.add_argument("--interval", type=int, default=30, help="每次 next_step 推进的秒数")
    parser.add_argument("--warmup_steps", type=int, default=10, help="注入车辆前先跑多少步")
    parser.add_argument("--departure_delay_steps", type=int, default=1, help="注入后延迟多少步出发")
    parser.add_argument("--max_steps", type=int, default=300, help="最多跟踪多少步")

    parser.add_argument("--output_json", help="可选：把最终结果写到 JSON 文件")
    return parser.parse_args()


def write_output_json(path: Optional[str], result: Dict[str, Any]) -> None:
    if not path:
        return
    Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def print_result(result: SingleVehicleResult) -> None:
    print("===POST_HUB_EVACUATION_RESULT_JSON===")
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


async def inject_one_vehicle(
    env: Env,
    template: Dict[str, Any],
    origin_road: str,
    destination_road: str,
    departure_delay_steps: int,
) -> SingleVehicleResult:
    """向模拟器注入一辆车，并返回 person_id 和出发时间等信息。"""

    (
        origin_lane_id,
        destination_lane_id,
        origin_lane_candidates,
        destination_lane_candidates,
    ) = await resolve_start_end_lanes(env, origin_road, destination_road)

    now = await env.eng.get_current_time()
    departure_time = int(now["t"]) + departure_delay_steps * env.step_size

    payload = build_person_auto_route(
        template=template,
        origin_lane_id=origin_lane_id,
        destination_lane_id=destination_lane_id,
        departure_time=departure_time,
    )

    # 核心调用：AddPerson 会把这个 person 加入模拟器。
    add_resp = await env.eng.client.person_service.AddPerson({"person": payload})
    person_id = add_resp.get("person_id") if isinstance(add_resp, dict) else None
    if not isinstance(person_id, int):
        raise RuntimeError(f"AddPerson failed: {add_resp}")

    return SingleVehicleResult(
        origin_road=origin_road,
        destination_road=destination_road,
        origin_lane_candidates=origin_lane_candidates,
        destination_lane_candidates=destination_lane_candidates,
        origin_lane_id=origin_lane_id,
        destination_lane_id=destination_lane_id,
        person_id=person_id,
        departure_time=departure_time,
        started_time=None,
        arrived_time=None,
        travel_time_seconds=None,
        status="not_arrived",
        trace=[],
    )


async def track_vehicle_until_done(env: Env, result: SingleVehicleResult, max_steps: int) -> None:
    """推进模拟器并跟踪车辆，直到到达终点 road 或超过最大步数。"""

    if result.person_id is None:
        result.status = "missing_person_id"
        return

    last_road: Optional[str] = None
    for _ in range(max_steps):
        env.step_once()

        sim_clock = await env.eng.get_current_time()
        now_t = int(sim_clock["t"])

        # 查询车辆当前所在 road；未出发/已离开路网时可能是 None。
        current_road = await env.eng.get_road_id_with_vehicle(result.person_id)
        current_road_str = str(current_road) if current_road is not None else None
        result.trace.append(
            {
                "t": now_t,
                "road": current_road_str,
            }
        )

        # 第一次查到车辆在路网上，记为 started_time。
        if result.started_time is None and current_road_str is not None:
            result.started_time = now_t

        # 当前 road 等于终点 road，认为到达。
        if result.started_time is not None and current_road_str == result.destination_road:
            result.arrived_time = now_t
            result.status = "reach_destination_road"
            break

        # 如果车辆出发后从路网中消失，也记录为完成。某些模拟器到达后会移除车辆。
        if result.started_time is not None and current_road_str is None and last_road is not None:
            result.arrived_time = now_t
            result.status = "disappeared_after_departure"
            break

        last_road = current_road_str

    if result.arrived_time is None and result.status == "not_arrived":
        result.status = "max_steps_reached_no_travel_time"

    # 最终耗时：车辆实际出现在路网上的时间 -> 到达/消失时间。
    if result.started_time is not None and result.arrived_time is not None:
        result.travel_time_seconds = max(0, result.arrived_time - result.started_time)


async def amain(args: argparse.Namespace) -> int:
    env: Optional[Env] = None
    result: Optional[SingleVehicleResult] = None

    # 给模拟器总步数留足空间：warmup + 出发延迟 + 跟踪步数 + 冗余。
    step_count = args.warmup_steps + args.departure_delay_steps + args.max_steps + 60

    try:
        env = Env(
            mongo_uri=args.mongo_uri,
            map_db=args.map_db,
            map_coll=args.map_coll,
            agent_db=args.agent_db,
            agent_coll=args.agent_coll,
            start_step=args.start,
            step_size=args.interval,
            step_count=step_count,
        )

        template = load_person_template_from_mongo(args.mongo_uri, args.agent_db, args.agent_coll)

        # 注入测试车辆前，先让背景交通流/信号控制跑一段。
        for _ in range(args.warmup_steps):
            env.step_once()

        result = await inject_one_vehicle(
            env=env,
            template=template,
            origin_road=str(args.origin_road),
            destination_road=str(args.destination_road),
            departure_delay_steps=args.departure_delay_steps,
        )

        await track_vehicle_until_done(env, result, max_steps=args.max_steps)

        write_output_json(args.output_json, result.to_dict())
        print_result(result)

        # 返回码 0 表示成功算出 travel_time_seconds；返回码 1 表示没得到有效时间。
        return 0 if result.travel_time_seconds is not None else 1

    except Exception as exc:
        if result is None:
            result = SingleVehicleResult(
                origin_road=str(args.origin_road),
                destination_road=str(args.destination_road),
                origin_lane_candidates=[],
                destination_lane_candidates=[],
                origin_lane_id=None,
                destination_lane_id=None,
                person_id=None,
                departure_time=None,
                started_time=None,
                arrived_time=None,
                travel_time_seconds=None,
                status=f"fatal_error: {exc}",
                trace=[],
            )
        else:
            result.status = f"fatal_error: {exc}"

        write_output_json(args.output_json, result.to_dict())
        print_result(result)
        return 1

    finally:
        if env is not None:
            try:
                env.stop()
            except Exception as exc:
                print(f"[WARN] simulator stop failed: {exc}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
