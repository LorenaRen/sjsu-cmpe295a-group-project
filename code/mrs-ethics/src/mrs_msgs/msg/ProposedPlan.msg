# The team's agreed plan, sent by the planner/coordinator to the gatekeeper on /mrs/proposed_plan.
# plan_json holds a TeamPlan (mrs_common.plan_schema), validated with pydantic on both ends.

string transaction_id    # UUID minted when the plan is first proposed; reused by every later message
string episode_id
int32 attempt
string plan_json
string dialogue_digest   # short summary (or hash) of the dialogue that produced the plan
string model_name        # e.g. "anthropic/claude-sonnet-5-5", "fake"
float64 latency_ms       # wall-clock planning time for this attempt
int32 tokens_in
int32 tokens_out
builtin_interfaces/Time stamp
