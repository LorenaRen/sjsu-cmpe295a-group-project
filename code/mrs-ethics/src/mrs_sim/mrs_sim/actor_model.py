"""SDF for the actors in a world's yaml: people and obstacle boxes (Module 2). Pure Python, no ROS imports.

actor_sdf(actor) fills templates/person.sdf or templates/obstacle.sdf. Actors are static: they stand
where the yaml puts them, block the LiDAR and the robots, and never move. The sizes below are the
single source of truth; the world tests use ACTOR_RADIUS to check actors keep clear of walls and paths.
"""
from __future__ import annotations

from importlib import resources
from string import Template

from mrs_sim.world_map import Actor

PERSON_RADIUS = 0.20      # [m] body cylinder
PERSON_HEIGHT = 1.70      # [m] top of the head
OBSTACLE_SIZE = 0.50      # [m] cube edge

# Footprint radius per actor type: the circle a robot must stay out of.
ACTOR_RADIUS = {
    "person": PERSON_RADIUS,
    "obstacle": OBSTACLE_SIZE / 2 * 2 ** 0.5,  # half diagonal: covers the box at any yaw
}


def _template(name: str) -> Template:
    return Template(resources.files("mrs_sim").joinpath("templates", name).read_text())


def actor_sdf(actor: Actor) -> str:
    """SDF model string for one actor; the model name is the actor id."""
    if actor.type == "person":
        head_r = 0.11
        body_h = PERSON_HEIGHT - 2 * head_r
        return _template("person.sdf").substitute(
            name=actor.id, radius=PERSON_RADIUS, body_height=body_h, body_z=body_h / 2,
            head_radius=head_r, head_z=body_h + head_r)
    return _template("obstacle.sdf").substitute(name=actor.id, size=OBSTACLE_SIZE, half=OBSTACLE_SIZE / 2)
