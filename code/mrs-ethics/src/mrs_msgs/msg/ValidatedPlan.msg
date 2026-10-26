# A plan the gatekeeper allowed, on /mrs/validated_plan. Only this reaches the executor.

string transaction_id
string episode_id
int32 attempt
string plan_json            # TeamPlan JSON (the original, or the modified one for MODIFY)
string source_verdict       # APPROVE or MODIFY; "FALLBACK" for the all-hold safe plan
builtin_interfaces/Time stamp
