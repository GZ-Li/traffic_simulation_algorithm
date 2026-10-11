"""仿真运行时封装模块"""
from .bus_injector import (
    BusInjectionConfig,
    add_bus_persons_with_plan,
    build_bus_injection_config_from_args,
    load_person_template_from_mongo,
    normalize_person_for_add,
    validate_bus_injection_args,
)
from .sim_runner import SimulationRunner

__all__ = [
    "BusInjectionConfig",
    "SimulationRunner",
    "add_bus_persons_with_plan",
    "build_bus_injection_config_from_args",
    "load_person_template_from_mongo",
    "normalize_person_for_add",
    "validate_bus_injection_args",
]
