# Something near a robot that the LiDAR sees but cannot classify: a person or an obstacle box.
# Scenario actors come from the scenario YAML (Module 3), not from vision (perfect-perception assumption).
# People are obstacles with a larger clearance; robots never interact with them.

string TYPE_PERSON=person
string TYPE_OBSTACLE=obstacle

string id          # unique within the scenario, e.g. "person_1", "box_2"
string type        # one of the TYPE_* constants
string at          # waypoint-graph node or edge it is on, e.g. "corridor_1" or "corridor_1->room_3"
float64 x          # map frame [m]
float64 y
float64 distance   # from the observing robot [m]
