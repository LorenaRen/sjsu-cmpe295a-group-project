# Progress of one task of one robot, published by the executor on /mrs/task_status (Module 6).

# STATE_REACHED = an intermediate waypoint was reached (see the waypoint field).
# (No comments after string constants: they would become part of the value.)
string STATE_STARTED=started
string STATE_REACHED=reached
string STATE_DONE=done
string STATE_FAILED=failed
string STATE_TIMED_OUT=timed_out

string transaction_id
string episode_id
string robot_id
string task_id
string state                      # STATE_* constants
string waypoint                   # graph node just reached / currently targeted
string detail
builtin_interfaces/Time stamp
