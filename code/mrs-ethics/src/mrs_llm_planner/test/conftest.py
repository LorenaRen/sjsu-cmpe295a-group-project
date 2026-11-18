"""Shared fixtures: the narrow_aisle map and a two-robot planning context."""
import pytest

from mrs_llm_planner.guidelines import BASIC_RULES, guidelines_for
from mrs_llm_planner.prompts import PlannerContext


@pytest.fixture(scope="session")
def world_map():
    from mrs_sim.world_map import load_world_map
    return load_world_map("narrow_aisle")[0]


@pytest.fixture
def team():
    return ("robot_a", "robot_b")


@pytest.fixture
def context(world_map, team):
    from mrs_llm_planner.routes import node_zones
    return PlannerContext("robot_a", team, world_map.to_prompt_dict(), tuple(guidelines_for(BASIC_RULES)),
                          node_zones=node_zones(world_map),
                          start_nodes={"robot_a": "room_w_s", "robot_b": "room_e_n"})
