# Published by the CBF filter on /mrs/safety_override whenever the safe command differs from
# the nominal one by more than a tolerance (Module 6). Velocities are planar single-integrator
# velocities of the look-ahead point, in the map frame (z unused).

string robot_id
string episode_id
geometry_msgs/Vector3 u_nominal
geometry_msgs/Vector3 u_safe
float32 min_distance              # smallest robot-robot or robot-obstacle distance [m]
string[] active_constraints       # e.g. ["pair:robot_a-robot_b", "lidar"]
builtin_interfaces/Time stamp
