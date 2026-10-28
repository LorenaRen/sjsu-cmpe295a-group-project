# Episode-level events from the runner and monitors, on /mrs/episode_event.

string KIND_START=start
string KIND_END=end
string KIND_VIOLATION=violation
string KIND_TIMEOUT=timeout
string KIND_PLANNING_EXHAUSTED=planning_exhausted
string KIND_EDGE_BLOCKED=edge_blocked
string KIND_EDGE_CLEARED=edge_cleared

string episode_id
string kind                       # KIND_* constants
string robot_id                   # "" for team-level events
string detail_json                # free-form JSON object with event details
builtin_interfaces/Time stamp
