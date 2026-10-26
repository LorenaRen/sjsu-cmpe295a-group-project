# All robots' WorldState in one message, on /mrs/team_state, for team-level consumers
# (gatekeeper, violation monitor, coordinator).

string episode_id
WorldState[] robots
builtin_interfaces/Time stamp
