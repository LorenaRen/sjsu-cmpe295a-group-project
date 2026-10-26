# Something a robot "perceives" that the LiDAR cannot classify (person, injured person,
# hazard, debris). Scenario actors come from the scenario YAML (Module 3), not from vision.

string TYPE_PERSON=person
string TYPE_INJURED_PERSON=injured_person
string TYPE_HAZARD=hazard
string TYPE_DEBRIS=debris

string id          # unique within the scenario, e.g. "patient_1"
string type        # one of the TYPE_* constants
string at          # waypoint-graph node or zone it is in, e.g. "room_3"
float64 x          # map frame [m]
float64 y
float64 distance   # from the observing robot [m]
