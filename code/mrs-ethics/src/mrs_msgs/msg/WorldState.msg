# Symbolic state of ONE robot, published by world_state_node (Module 3) on
# /<robot_id>/world_state at 2 Hz. Together with the static map in the system prompt, this is
# everything an LLM agent learns about the world.

string SECTOR_CLEAR=clear
string SECTOR_NEAR=near
string SECTOR_BLOCKED=blocked

string robot_id
string episode_id

# Ground-truth pose in the map frame (localization is assumed perfect).
float64 x
float64 y
float64 yaw                    # [rad]

string at                      # nearest graph node within 0.5 m, else the edge "node_a->node_b"
string[] zones                 # ids of every semantic zone the robot is inside, e.g. ["corridor_1"]; [] if none

# LiDAR sector summary; the three arrays are parallel (same length, same order).
string[] sector_names          # e.g. ["front", "left", "back", "right"]
string[] sector_status         # SECTOR_* constants
float32[] sector_min_range     # [m]

PerceivedEntity[] obstacles    # people and obstacle boxes within perception range

builtin_interfaces/Time stamp
