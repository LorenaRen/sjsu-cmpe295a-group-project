# One turn of the inter-agent negotiation on /mrs/dialogue (Module 4).
# A turn with agree set to true carries the plan the speaker agrees to in plan_json.

string episode_id
string transaction_id    # "" until the first proposal of this planning cycle is minted
int32 attempt            # 0 for the first plan, +1 after every REJECT/MODIFY
int32 round              # 1 .. max_rounds within an attempt
string speaker_id        # robot id of the agent speaking
string text              # natural-language content of the turn
bool agree
string plan_json         # TeamPlan JSON when agree is true, else empty
builtin_interfaces/Time stamp
