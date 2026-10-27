# All robots' WorldState in one message, on /mrs/team_state, for team-level consumers
# (gatekeeper, violation monitor, coordinator).

string episode_id
WorldState[] robots

# Graph edges that are currently impassable (Module 3), as undirected edge ids from
# mrs_common.names.edge_id, e.g. "corridor_1--room_3". An edge is blocked in both directions.
string[] blocked_edges
builtin_interfaces/Time stamp
