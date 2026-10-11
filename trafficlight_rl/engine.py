#!/usr/bin/env python3
"""
Engine module for traffic light optimization using simulet-go.
This module provides a high-level interface to interact with the simulet-go simulator
for traffic light control and optimization tasks.
"""

import sys
import os

from tenacity import retry_if_exception

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import base64
import logging
import os
import socket
import time
import yaml
from subprocess import Popen
from typing import Dict, List, Optional, Any, Tuple
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pymongo
from pycitysim.sim import CityClient
from pycitysim.sidecar import OnlyClientSidecar
from pycityproto.city.map.v2.map_pb2 import Map
from mosstool.util.format_converter import coll2pb


def find_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def encode_to_base64(input_string: str) -> str:
    """Encode string to base64."""
    input_bytes = input_string.encode("utf-8")
    base64_bytes = base64.b64encode(input_bytes)
    return base64_bytes.decode("utf-8")


class SimulatorConfig:
    """Configuration for the simulet-go simulator."""

    def __init__(
        self,
        mongo_uri: str,
        map_db: str,
        map_coll: str,
        agent_db: str,
        agent_coll: str,
        start_step: int,
        total_step: int,
        interval: int,
        use_max_pressure: bool,
        output_sql_dsn: str,
        output_job_prefix: str,
        output_bbox: Optional[Tuple[float, float, float, float]],
        **kwargs,
    ):
        """
        Args:
            mongo_uri: MongoDB connection URI
            map_db: Map database name
            map_coll: Map collection name
            agent_db: Agent database name
            agent_coll: Agent collection name
            start_step: Starting simulation step
            total_step: Total simulation steps
            interval: Simulation interval
            use_max_pressure: Whether to use max pressure
            output_sql_dsn: SQL DSN for output (empty string means no output)
            output_job_prefix: Output job name prefix (default: "traffic_light_optimization_"), the final job name will be output_job_prefix + num
            output_bbox: Output bounding box, (min_longitude, min_latitude, max_longitude, max_latitude) (default: None)
            **kwargs: Additional configuration parameters
        """
        if output_sql_dsn != "":
            if output_bbox is None:
                raise ValueError("output_bbox is required when output_sql_dsn is not empty")
            if output_bbox[0] >= output_bbox[2] or output_bbox[1] >= output_bbox[3]:
                raise ValueError("output_bbox is invalid")
            self.output_bbox = output_bbox
        else:
            self.output_bbox = None
        
        self.mongo_uri = mongo_uri
        self.map_db = map_db
        self.map_coll = map_coll
        self.agent_db = agent_db
        self.agent_coll = agent_coll
        
        # Load map data from MongoDB
        client = pymongo.MongoClient(mongo_uri)
        map_collection = client[map_db][map_coll]
        self.map_data = coll2pb(map_collection, Map())
        
        client.close()
        
        self.start_step = start_step
        self.total_step = total_step
        self.interval = interval
        self.kwargs = kwargs
        self.use_max_pressure = use_max_pressure
        self.output_sql_dsn = output_sql_dsn
        self.output_job_prefix = output_job_prefix
        self._counter = -1

    def get_output_job(self) -> str:
        """Get the output job name."""
        self._counter += 1
        return self.output_job_prefix + str(self._counter)

    def to_yaml(self) -> str:
        """Generate YAML configuration for simulet-go."""
        if self.output_sql_dsn:
            output = {
                "target": {
                    "sql": self.output_sql_dsn,
                },
                "simple": {
                    "road_status_v_min": 0,
                    "road_status_interval": 1,
                },
            }
        else:
            output = {}
        config_dict = {
            "input": {
                "uri": self.mongo_uri,
                "map": {
                    "db": self.map_db,
                    "col": self.map_coll,
                },
                "person": {
                    "db": self.agent_db,
                    "col": self.agent_coll,
                },
            },
            "control": {
                "step": {
                    "start": self.start_step,
                    "total": self.total_step + 1,
                    "interval": self.interval,
                },
                "skip_overtime_trip_when_init": True,
                "enable_platoon": False,
                "enable_indoor": False,
                "prefer_fixed_light": not self.use_max_pressure,
                "enable_collision_avoidance": False,
                "enable_go_astray": False,
                "lane_change_model": "mobil",
                "microscopic_range": {
                    "min_longitude": self.output_bbox[0],
                    "min_latitude": self.output_bbox[1],
                    "max_longitude": self.output_bbox[2],
                    "max_latitude": self.output_bbox[3],
                } if self.output_bbox else None,
                **self.kwargs,
            },
            "output": output,
        }
        return yaml.dump(config_dict, allow_unicode=True)


class Engine:
    """
    Main engine class for traffic light optimization.

    This class provides an interface to the simulet-go simulator,
    supporting traffic light control and various simulation queries.
    """

    def __init__(self, config: SimulatorConfig, log_dir: str = "./log"):
        self.config = config
        self.map_data = config.map_data
        self.log_dir = log_dir
        self.client: Optional[CityClient] = None
        self.simulator_proc: Optional[Popen] = None
        self.syncer_proc: Optional[Popen] = None
        self.sidecar: Optional[OnlyClientSidecar] = None
        self.sim_addr: Optional[str] = None
        self.syncer_addr: Optional[str] = None
        self.checkpoints: Dict[str, Any] = {}
        self.current_step = 0
        self.is_running = False
        self._map_data = None
        self._executor = ThreadPoolExecutor(max_workers=4)

        # Initialize logging
        os.makedirs(log_dir, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        self.logger = logging.getLogger(__name__)

        self.lane_id2index = {lane.id: i for i, lane in enumerate(self.map_data.lanes)}

    def _start_simulator(self):
        """Start the simulet-go simulator process."""

        # Find free ports
        sim_port = find_free_port()
        syncer_port = find_free_port()

        self.sim_addr = f"localhost:{sim_port}"
        self.syncer_addr = f"localhost:{syncer_port}"

        # Generate configuration
        config_base64 = encode_to_base64(self.config.to_yaml())

        # Start syncer (if available)
        # syncer_path = os.path.expanduser("../bin/simulet-go-bin/syncer")
        syncer_path = os.path.expanduser("/home/nkk/work/bin/simulet-go-bin/syncer")
        if os.path.exists(syncer_path):
            self.syncer_proc = Popen(
                [
                    syncer_path,
                    "-address",
                    f"localhost:{syncer_port}",
                    "-app",
                    "city",
                    "-app",
                    "engine",
                ]
            )
            self.logger.info(f"Started syncer at {self.syncer_addr}")
            time.sleep(1)

        # Start simulet-go
        # simulet_path = os.path.expanduser("../bin/simulet-go-bin/simulet-go")
        simulet_path = os.path.expanduser("/home/nkk/work/bin/simulet-go-bin/simulet-go")
        if not os.path.exists(simulet_path):
            simulet_path = "./simulet-go"  # Try relative path

        commands = [
            simulet_path,
            "-config-data",
            config_base64,
            "-job",
            self.config.get_output_job(),
            "-listen",
            self.sim_addr,
            "-output",
            self.log_dir,
            "-log.level",
            "info",
        ]

        if self.syncer_proc:
            commands.extend(["-syncer", f"http://{self.syncer_addr}"])

        self.simulator_proc = Popen(commands)
        self.logger.info(f"Started simulet-go at {self.sim_addr}")
        time.sleep(3)  # Wait for simulator to start

        # Initialize client
        self.client = CityClient(f"http://{self.sim_addr}")
        self.sidecar = OnlyClientSidecar("engine", self.syncer_addr)
        self.sidecar.init()
        self.is_running = True

    def _stop_simulator(self):
        """Stop the simulator processes."""
        try:
            assert self.sidecar is not None
            self.sidecar.step(True)
            self.sidecar.close()
        except Exception as e:
            self.logger.error(f"Error closing sidecar: {e}")

        if self.syncer_proc:
            self.syncer_proc.terminate()
            self.syncer_proc.wait()
            self.syncer_proc = None

        if self.simulator_proc:
            self.simulator_proc.terminate()
            self.simulator_proc.wait(1)
            self.simulator_proc.kill()
            self.simulator_proc.wait(1)
            self.simulator_proc = None

        self.is_running = False
        self.logger.info("Simulator stopped")

    def __enter__(self):
        """Context manager entry."""
        self._start_simulator()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self._stop_simulator()

    def __del__(self):
        """Destructor to ensure cleanup."""
        self._stop_simulator()

    # Core simulation methods
    def get_map(self) -> Map:
        """Get the map data from the simulator."""
        return self.map_data

    def get_junction_phase_lanes(self, junction_ids) -> List[List[Tuple[List[int], List[int]]]]:
        """
        Get the `index` of the `in` and `out` lanes of each phase of each junction
        """
        from pycityproto.city.map.v2.light_pb2 import LightState
        from pycityproto.city.map.v2.map_pb2 import LaneType

        result = []

        for junction in self.map_data.junctions:
            if len(junction_ids) > 0:
                if junction.id not in junction_ids:
                    continue

            junction_phases = []

            # Get phases - prioritize fixed_program.phases over phases
            phases_to_use = []
            if junction.HasField("fixed_program") and junction.fixed_program.phases:
                phases_to_use = junction.fixed_program.phases
            elif junction.phases:
                phases_to_use = junction.phases

            for phase in phases_to_use:
                phase_in_lanes = []
                phase_out_lanes = []

                # Get states from the phase
                states = phase.states

                # Iterate through states and lane_ids simultaneously
                for i, state in enumerate(states):
                    if state == LightState.LIGHT_STATE_GREEN:
                        # This lane is green in this phase
                        if i < len(junction.lane_ids):
                            lane_id = junction.lane_ids[i]

                            # Find the corresponding lane in map_data.lanes
                            lane = None
                            for l in self.map_data.lanes:
                                if l.id == lane_id:
                                    lane = l
                                    break

                            if (
                                lane is not None
                                and lane.type == LaneType.LANE_TYPE_DRIVING
                            ):
                                # Get predecessor lanes (in_lanes)
                                for pred_conn in lane.predecessors:
                                    pred_lane_id = pred_conn.id
                                    if pred_lane_id in self.lane_id2index:
                                        pred_index = self.lane_id2index[pred_lane_id]
                                        phase_in_lanes.append(pred_index)

                                # Get successor lanes (out_lanes)
                                for succ_conn in lane.successors:
                                    succ_lane_id = succ_conn.id
                                    if succ_lane_id in self.lane_id2index:
                                        succ_index = self.lane_id2index[succ_lane_id]
                                        phase_out_lanes.append(succ_index)

                # Remove duplicates and sort
                phase_in_lanes = sorted(list(set(phase_in_lanes)))
                phase_out_lanes = sorted(list(set(phase_out_lanes)))

                junction_phases.append((phase_in_lanes, phase_out_lanes))

            result.append(junction_phases)

        return result

    def get_junction_inout_lanes(self, junction_ids) -> Tuple[List[List[int]], List[List[int]]]:
        """
        Get the `index` of the `in` and `out` lanes of each junction
        """
        from pycityproto.city.map.v2.map_pb2 import LaneType

        in_lanes = []
        out_lanes = []

        for junction in self.map_data.junctions:
            junction_in_lanes = []
            junction_out_lanes = []

            if len(junction_ids) > 0:
                if junction.id not in junction_ids:
                    continue
            # 遍历junction的所有车道
            for lane_id in junction.lane_ids:
                # 在map_data.lanes中找到对应的lane
                lane = None
                for l in self.map_data.lanes:
                    if l.id == lane_id:
                        lane = l
                        break

                if lane is not None and lane.type == LaneType.LANE_TYPE_DRIVING:
                    # 获取前驱车道 (进入车道)
                    for pred_conn in lane.predecessors:
                        pred_lane_id = pred_conn.id
                        if pred_lane_id in self.lane_id2index:
                            pred_index = self.lane_id2index[pred_lane_id]
                            junction_in_lanes.append(pred_index)

                    # 获取后继车道 (离开车道)
                    for succ_conn in lane.successors:
                        succ_lane_id = succ_conn.id
                        if succ_lane_id in self.lane_id2index:
                            succ_index = self.lane_id2index[succ_lane_id]
                            junction_out_lanes.append(succ_index)

            # 去重并排序
            junction_in_lanes = sorted(list(set(junction_in_lanes)))
            junction_out_lanes = sorted(list(set(junction_out_lanes)))

            in_lanes.append(junction_in_lanes)
            out_lanes.append(junction_out_lanes)

        return (in_lanes, out_lanes)

    def get_lane_lengths(self, junction_ids) -> List[float]:
        """Get the length of each lane."""
        from pycityproto.city.map.v2.map_pb2 import LaneType
        lanes = self.get_junction_lanes(junction_ids)
        lane_lengths = []
        for lane_index in lanes:
            lane = self.map_data.lanes[lane_index]

            lane_lengths.append(lane.length)

        return lane_lengths

    def get_junction_phase_counts(self, _junction_ids) -> List[int]:
        """Get the number of phases for each junction."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        phase_counts = []
        for j in self.map_data.junctions:
            if len(_junction_ids) > 0:
                if j.id not in _junction_ids:
                    continue
            phase_counts.append(len(j.phases))

        return phase_counts

    async def get_vehicle_ids_of_road(self, road_id):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        road_service = self.client.road_service
        road_state = await road_service.GetRoad({"road_ids":[road_id],"exclude_person": False,"exclude_lane":False})
        lane_state = road_state["states"][0]["lanes"]
        vehicle_ids = [ ]
        for lane in lane_state:
            vehicles = lane["persons"]
            for vehicle in vehicles:
                vehicle_ids.append(vehicle["id"])

        return vehicle_ids

    async def get_lane_id_of_road(self,road_id):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        road_service = self.client.road_service
        road_states = await road_service.GetRoad({"road_ids": [road_id], "exclude_person": False, "exclude_lane": False})

        lane_ids = []
        for states in road_states["states"]:
            lane_states = states[ "lanes" ]
            for lane in lane_states:
                lane_ids.append(lane["id"])
        return lane_ids

    async def get_current_time(self):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        now_time = await self.client.clock_service.Now({})
        #t seconds
        return {"day": now_time["day"],"t": now_time["t"]}

    async def get_vehicle_pos(self,vehicle_id):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        vehicles = await self.client.person_service.GetAllVehicles({})
        for vehicle in vehicles["vehicles"]:
            if vehicle_id == vehicle["base"]["id"]:
                return vehicle["base"]["position"]

    async def get_road_avg_v(self, road_id):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        road_service = self.client.road_service
        road_states = await road_service.GetRoad({"road_ids": [road_id], "exclude_person": False, "exclude_lane": False})
        return  road_states["states"][0]["avg_v"]

    async def get_vehicle_speed(self, vehicle_id):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        person_state = await self.client.person_service.GetPersons({"person_ids":[vehicle_id]})
        return person_state["persons"][0]["motion"]["v"]

    async def get_lanes(self):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        res_state = await self.client.lane_service.GetLane({"lane_ids":[],"exclude_person": False})

        lane_ids = []
        for lane_state in res_state["states"]:
            lane_ids.append(lane_state["id"])

        return lane_ids

    async def get_roads(self):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        road_service = self.client.road_service
        road_states = await road_service.GetRoad({"road_ids": [ ], "exclude_person": False, "exclude_lane": False})

        road_ids = []
        for state in road_states["states"]:
            road_ids.append(state["id"])

        return road_ids

    async def get_road_id_with_vehicle(self, vehicle_id):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        road_service = self.client.road_service
        road_states = await road_service.GetRoad({"road_ids": [], "exclude_person": False, "exclude_lane": False})
        for states in road_states["states"]:
            lane_state = states[ "lanes" ]
            for lane in lane_state:
                vehicles = lane[ "persons" ]
                for vehicle in vehicles:
                    if vehicle_id == vehicle["id"]:
                        return states["id"]

    async def get_lane_id_with_vehicle(self, vehicle_id):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        lane_states = await self.client.lane_service.GetLane({"lane_ids":[],"exclude_person": False})
        for states in lane_states["states"]:
            vehicles = states[ "persons" ]
            for vehicle in vehicles:
                if vehicle_id == vehicle[ "id" ]:
                    return states[ "id" ]

    async def set_vehicle_acc(self, vehicle_id, acc: float):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        await self.client.person_service.SetControlledVehicleIDs({"vehicle_ids": vehicle_id})

        envs = await self.client.person_service.FetchControlledVehicleEnvs({})
        logging.info("FetchControlledVehicleEnvs: %s", envs)
        actions = []
        for env in envs["vehicle_envs"]:
            actions.append({"id": env["id"], "acc": 10, "angle": 0})
        await self.client.person_service.SetControlledVehicleActions(
            {"vehicle_actions": actions})

    async def set_max_vehicle_speed(self, vehicle_id, max_v:float):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        lane_id = await self.get_lane_id_with_vehicle(vehicle_id)
        return await self.client.lane_service.SetLaneMaxV({"lane_id": lane_id, "max_v": max_v})

    def get_junction_lanes(self, junction_ids):
        """Get the number of waiting vehicles for each lane."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")
        from pycityproto.city.map.v2.map_pb2 import LaneType
        lanes = [ ]
        for junction in self.map_data.junctions:
            junction_in_lanes = [ ]
            junction_out_lanes = [ ]

            if len(junction_ids) > 0:
                if junction.id not in junction_ids:
                    continue
            # 遍历junction的所有车道
            for lane_id in junction.lane_ids:
                # 在map_data.lanes中找到对应的lane
                lane = None
                for l in self.map_data.lanes:
                    if l.id == lane_id:
                        lane = l
                        break
                if self.lane_id2index[ lane.id ] in lanes:
                    continue

                if lane is not None and lane.type == LaneType.LANE_TYPE_DRIVING:
                    lanes.append(self.lane_id2index[ lane.id ])
                    # 获取前驱车道 (进入车道)
                    for pred_conn in lane.predecessors:
                        pred_lane_id = pred_conn.id
                        if pred_lane_id in self.lane_id2index:
                            pred_index = self.lane_id2index[ pred_lane_id ]
                            lanes.append(pred_index)

                    # 获取后继车道 (离开车道)
                    for succ_conn in lane.successors:
                        succ_lane_id = succ_conn.id
                        if succ_lane_id in self.lane_id2index:
                            succ_index = self.lane_id2index[ succ_lane_id ]
                            lanes.append(succ_index)

            # 去重并排序
            lanes = sorted(list(set(lanes)))

        return lanes
    async def get_lane_vehicle_counts(self, junction_ids) -> np.ndarray:
        """Get the number of waiting vehicles for each lane."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        lanes = self.get_junction_lanes(junction_ids)


        assert self.client is not None
        res = await self.client.lane_service.GetLane({"exclude_person": True})
        assert isinstance(res, dict)
        arr = np.zeros(len(lanes), dtype=np.int32)
        i = 0
        for lane_index in lanes:
            for lane in res["states"]:
                if self.lane_id2index[lane["id"]] != lane_index:
                    continue
                arr[i] = lane["vehicle_cnt"]
            i += 1
        return arr

    async def get_junction_in_lane_vehicle_counts(self, junction_ids, in_lanes):
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        #lanes = self.get_junction_lanes(junction_ids)
        in_lanes_junction = []
        for junction in junction_ids:
            in_lane = in_lanes[junction]
            for lane_index in in_lane:
                in_lanes_junction.append(lane_index)

        assert self.client is not None
        res = await self.client.lane_service.GetLane({"exclude_person": True})
        assert isinstance(res, dict)
        arr = np.zeros(len(in_lanes_junction), dtype=np.int32)
        i = 0
        for lane_index in in_lanes_junction:
            for lane in res[ "states" ]:
                if self.lane_id2index[ lane[ "id" ] ] != lane_index:
                    continue
                arr[i] = lane["total_queuing_vehicle_cnt"]
            i += 1
        return arr

    async def get_lane_waiting_vehicle_counts(self, junction_ids) -> np.ndarray:
        """Get the number of waiting vehicles for each lane."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        lanes = self.get_junction_lanes(junction_ids)
        assert self.client is not None
        res = await self.client.lane_service.GetLane({"exclude_person": True})
        assert isinstance(res, dict)
        arr = np.zeros(len(lanes), dtype=np.int32)
        i = 0
        for lane_index in lanes:
            for lane in res[ "states" ]:
                if self.lane_id2index[ lane[ "id" ] ] != lane_index:
                    continue
                arr[i] = lane["total_queuing_vehicle_cnt"]
            i += 1
        return arr

    async def set_tl_phase_batch(self, junction_indices: List[int], actions: List[int]):
        """Set traffic light phases for multiple junctions."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        if len(junction_indices) != len(actions):
            raise ValueError("Junction IDs and actions must have same length")

        # In real implementation, this would call simulator API
        # For now, just log the action
        self.logger.debug(f"Setting TL phases: {dict(zip(junction_indices, actions))}")

        assert self.client is not None
        tasks = []
        for junction_index, action in zip(junction_indices, actions):
            tasks.append(
                self.client.light_service.SetTrafficLightPhase(
                    {
                        "junction_id": self.map_data.junctions[junction_index].id,
                        "phase_index": action,
                        "time_remaining": 1e99,
                    }
                )
            )
        await asyncio.gather(*tasks)

    def next_step(self, step_size: int = 1):
        """Advance simulation by the specified number of steps."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        # In real implementation, this would call simulator step API
        self.current_step += step_size
        self.logger.debug(f"Advanced to step {self.current_step}")
        assert self.sidecar is not None
        for _ in range(step_size):
            self.sidecar.notify_step_ready()
            self.sidecar.step()

    async def get_departed_vehicle_average_traveling_time(self) -> float:
        """Get average traveling time for departed vehicles."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")

        assert self.client is not None
        res = await self.client.person_service.GetGlobalStatistics({})
        print("get_departed_vehicle_average_traveling_time: ", res)
        assert isinstance(res, dict)
        departed_vehicles = res["num_completed_trips"] + res["num_vehicles"]
        return res["running_total_travel_time"] / departed_vehicles

    async def get_finished_vehicle_average_traveling_time(self) -> float:
        """Get average traveling time for finished vehicles."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")
        assert self.client is not None
        res = await self.client.person_service.GetGlobalStatistics({})
        print("get_finished_vehicle_average_traveling_time: ", res)
        assert isinstance(res, dict)
        return res["completed_avg_travel_time"]

    async def get_finished_vehicle_count(self) -> int:
        """Get count of finished vehicles."""
        if not self.is_running:
            raise RuntimeError("Simulator not running")
        assert self.client is not None
        res = await self.client.person_service.GetGlobalStatistics({})
        print("get_finished_vehicle_count: ", res)
        assert isinstance(res, dict)
        return res["num_completed_trips"]

    # Utility methods
    def get_current_step(self) -> int:
        """Get current simulation step."""
        return self.current_step

    def get_simulation_time(self) -> float:
        """Get current simulation time in seconds."""
        return self.current_step * self.config.interval

    def is_simulation_running(self) -> bool:
        """Check if simulation is running."""
        return self.is_running


def get_engine(
    mongo_uri: str,
    map_db: str,
    map_coll: str,
    agent_db: str,
    agent_coll: str,
    start_step: int = 0,
    total_step: int = 3600,
    interval: int = 1,
    use_max_pressure: bool = False,
    output_sql_dsn: str = "",
    output_job_prefix: str = "traffic_light_optimization_",
    output_bbox: Optional[Tuple[float, float, float, float]] = None,
    **kwargs,
) -> Engine:
    """
    Create and initialize an Engine instance.

    Args:
        mongo_uri: MongoDB connection URI
        map_db: Map database name
        map_coll: Map collection name
        agent_db: Agent database name
        agent_coll: Agent collection name
        start_step: Starting simulation step (default: 0)
        total_step: Total simulation steps (default: 3600)
        interval: Simulation interval (default: 1)
        use_max_pressure: Whether to use max pressure (default: False)
        output_sql_dsn: SQL DSN for output (default: "")
        output_job_prefix: Output job name prefix (default: "traffic_light_optimization_"), the final job name will be output_job_prefix + num
        output_bbox: Output bounding box, (min_longitude, min_latitude, max_longitude, max_latitude) (default: None)
        **kwargs: Additional configuration parameters

    Returns:
        Engine: Initialized engine instance
    """
    config = SimulatorConfig(
        mongo_uri=mongo_uri,
        map_db=map_db,
        map_coll=map_coll,
        agent_db=agent_db,
        agent_coll=agent_coll,
        start_step=start_step,
        total_step=total_step,
        interval=interval,
        use_max_pressure=use_max_pressure,
        output_sql_dsn=output_sql_dsn,
        output_job_prefix=output_job_prefix,
        output_bbox=output_bbox,
        **kwargs,
    )

    engine = Engine(config)
    engine._start_simulator()

    return engine
