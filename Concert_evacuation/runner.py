"""
演唱会散场人流疏散场景 - 仅保留加人相关逻辑
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import random
import math
from typing import Dict, Any, Optional, List, Tuple

import pymongo

from pycitysim.sim import CityClient


def normalize_person_for_add(person: Dict[str, Any]) -> Dict[str, Any]:
    """清洗Person字段，确保符合AddPerson的schema（保留所有字段，只去除base和id）"""
    field_map = {
        "vehicle_attribute": "vehicleAttribute",
        "bus_attribute": "busAttribute",
        "pedestrian_attribute": "pedestrianAttribute",
        "bike_attribute": "bikeAttribute",
        "output_when_sleep": "outputWhenSleep",
    }

    normalized: Dict[str, Any] = {}
    for key, value in person.items():
        if key in field_map:
            normalized[field_map[key]] = value
        else:
            normalized[key] = value

    # AddPerson要求不设置id，并且不接受base字段（其他字段全部保留）
    normalized.pop("id", None)
    normalized.pop("base", None)

    return normalized


def load_person_template_from_mongo(
    mongo_uri: str, agent_db: str, agent_coll: str
) -> Optional[Dict[str, Any]]:
    """从Mongo读取一条静态person配置（data字段）"""
    logger = logging.getLogger(__name__)
    client = pymongo.MongoClient(mongo_uri)
    try:
        db = client[agent_db]
        coll = db[agent_coll]
        doc = coll.find_one({}, {"_id": 0})
        if not doc:
            logger.warning("Mongo中未找到person模板")
            return None
        data = doc.get("data")
        if not isinstance(data, dict):
            logger.warning("Mongo模板缺少data字段")
            return None
        return data
    finally:
        client.close()


def generate_home_location(
    venue_lat: float,
    venue_lon: float,
    distance_km: float = 30
) -> Tuple[float, float]:
    """在场馆周围距离distance_km范围内随机生成home位置"""
    lat_delta = distance_km / 111.0
    lon_delta = distance_km / (111.0 * math.cos(math.radians(venue_lat)))

    random_lat = venue_lat + (random.random() - 0.5) * 2 * lat_delta
    random_lon = venue_lon + (random.random() - 0.5) * 2 * lon_delta

    return random_lat, random_lon


def generate_schedule_with_venue_location(
    venue_lat: float,
    venue_lon: float,
    start_hour: int = 19,
    end_hour: int = 21
) -> List[Dict[str, Any]]:
    """生成schedule数据，地点为场馆坐标，时间为start_hour到end_hour"""
    return [
        {
            "location": {
                "latitude": venue_lat,
                "longitude": venue_lon
            },
            "time_range": {
                "start": f"{start_hour:02d}:00",
                "end": f"{end_hour:02d}:00"
            }
        }
    ]


def generate_concert_evacuation_persons(
    template: Dict[str, Any],
    num_persons: int,
    venue_lat: float = 30.476075,
    venue_lon: float = 114.461183,
    home_distance_km: float = 30,
    label: str = "car",
    seed: Optional[int] = None
) -> List[Dict[str, Any]]:
    """基于模板生成演唱会散场疏散的person数据"""
    logger = logging.getLogger(__name__)

    if seed is not None:
        random.seed(seed)

    logger.info(f"开始生成 {num_persons} 条演唱会疏散person数据...")
    logger.info(f"场馆坐标: ({venue_lat}, {venue_lon})")
    logger.info(f"home随机范围: ±{home_distance_km}km (方形范围)")

    persons = []

    for i in range(num_persons):
        person = copy.deepcopy(template)

        home_lat, home_lon = generate_home_location(venue_lat, venue_lon, home_distance_km)
        if "home" not in person:
            person["home"] = {}
        person["home"]["latitude"] = home_lat
        person["home"]["longitude"] = home_lon

        person["schedule"] = generate_schedule_with_venue_location(venue_lat, venue_lon)

        if "labels" not in person:
            person["labels"] = []
        person["labels"] = [label]

        person.pop("id", None)
        person.pop("base", None)

        persons.append(person)

        if (i + 1) % 1000 == 0:
            logger.info(f"已生成 {i + 1}/{num_persons} 条person数据")

    logger.info(f"完成生成 {num_persons} 条person数据")
    return persons


async def add_concert_evacuation_persons_from_url(
    citysim_url: str,
    count: int,
    mongo_uri: str,
    agent_db: str,
    agent_coll: str,
    venue_lat: float = 30.476075,
    venue_lon: float = 114.461183,
    home_distance_km: float = 30,
    label: str = "car",
    seed: Optional[int] = None
) -> List[int]:
    """通过AddPerson新增演唱会疏散人员，并返回新增person_id列表"""
    logger = logging.getLogger(__name__)
    if count <= 0:
        return []

    template = load_person_template_from_mongo(mongo_uri, agent_db, agent_coll)
    if not template:
        logger.warning("无法从Mongo读取person模板，AddPerson失败")
        return []

    persons = generate_concert_evacuation_persons(
        template=template,
        num_persons=count,
        venue_lat=venue_lat,
        venue_lon=venue_lon,
        home_distance_km=home_distance_km,
        label=label,
        seed=seed
    )

    client = CityClient(citysim_url)
    logger.info(f"开始通过AddPerson添加 {count} 条疏散人员...")
    added_ids: List[int] = []

    for idx, person in enumerate(persons):
        person_normalized = normalize_person_for_add(person)
        try:
            resp = await client.person_service.AddPerson({"person": person_normalized})
            pid = resp.get("person_id") if isinstance(resp, dict) else None
            if isinstance(pid, int):
                added_ids.append(pid)
        except Exception as exc:
            logger.warning(f"AddPerson失败 (index={idx}): {exc}")

        if (idx + 1) % 500 == 0:
            logger.info(f"已添加 {idx + 1}/{count} 条person，成功ID数: {len(added_ids)}")

    logger.info(f"AddPerson完成: 成功添加 {len(added_ids)} 条person数据")
    if added_ids:
        logger.info(f"新增person_id示例: {added_ids[:5]}")

    return added_ids


def _env_required(key: str) -> str:
    value = os.environ.get(key, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量: {key}")
    return value


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    citysim_url = _env_required("CITYSIM_URL")
    mongo_uri = _env_required("MONGO_URI")
    agent_db = _env_required("AGENT_DB")
    agent_coll = _env_required("AGENT_COLL")
    count = int(os.environ.get("PERSON_COUNT", "0"))

    venue_lat = float(os.environ.get("VENUE_LAT", "30.476075"))
    venue_lon = float(os.environ.get("VENUE_LON", "114.461183"))
    home_distance_km = float(os.environ.get("HOME_DISTANCE_KM", "30"))
    label = os.environ.get("PERSON_LABEL", "car")
    seed = os.environ.get("PERSON_SEED")
    seed_val = int(seed) if seed not in (None, "") else None

    added = asyncio.run(
        add_concert_evacuation_persons_from_url(
            citysim_url=citysim_url,
            count=count,
            mongo_uri=mongo_uri,
            agent_db=agent_db,
            agent_coll=agent_coll,
            venue_lat=venue_lat,
            venue_lon=venue_lon,
            home_distance_km=home_distance_km,
            label=label,
            seed=seed_val
        )
    )
    print(json.dumps({"added_person_ids": added, "count": len(added)}, ensure_ascii=True))
