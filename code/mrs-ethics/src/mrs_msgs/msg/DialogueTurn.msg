# One turn of the inter-agent negotiation on /mrs/dialogue (Module 4).
# Every agent and the coordinator receive every turn; there are no private messages.
# The coordinator posts a prompt before every agent turn, naming the next speaker; the
# prompt that opens an attempt also carries the mission and, after a REJECT, the constraints.
# A named agent posts exactly one turn: a proposal (carries a plan), an agreement (accepts
# the latest proposal of this attempt unchanged, carries no plan) or discussion (text only).
# The dialogue succeeds when every agent has accepted the same proposal (Module 4).

string KIND_PROMPT=prompt
string KIND_PROPOSAL=proposal
string KIND_AGREEMENT=agreement
string KIND_DISCUSSION=discussion

string COORDINATOR=coordinator

string episode_id
string transaction_id    # minted by the coordinator per planning cycle; same for every attempt
int32 attempt            # 0 for the first dialogue, +1 after every REJECT or failed attempt
int32 round              # 1 .. max_rounds within an attempt
string kind              # KIND_* constants
string speaker_id        # robot id of the agent speaking, or COORDINATOR for prompts
string next_speaker      # prompts only: robot id of the agent that speaks next, else empty
string text              # natural-language content; the opening prompt holds mission and constraints
string plan_json         # proposals only: TeamPlan JSON, else empty
builtin_interfaces/Time stamp
