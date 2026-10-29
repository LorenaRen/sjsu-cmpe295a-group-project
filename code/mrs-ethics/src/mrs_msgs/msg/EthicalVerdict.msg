# Gatekeeper decision on one ProposedPlan, on /mrs/verdict (Module 5).

string VERDICT_APPROVE=APPROVE
string VERDICT_MODIFY=MODIFY
string VERDICT_REJECT=REJECT

string transaction_id
string episode_id
int32 attempt
string verdict              # VERDICT_* constants
string[] rule_ids           # rules that fired, e.g. ["keep_clear_no_stop"]
string[] standard_refs      # only clauses the team has actually read (see guide, Module 5)
float32 risk_score          # 0.0 (none) .. 1.0 (severe)
string rationale            # human/LLM-readable explanation, fed back to the planner
string constraints_json     # list of Constraint objects (mrs_common.plan_schema) for MODIFY/REJECT
float64 latency_ms          # time the check took
builtin_interfaces/Time stamp
