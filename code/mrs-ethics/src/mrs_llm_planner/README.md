# mrs_llm_planner (Module 4)

One LLM agent per robot. Each agent proposes only its own robot's part; the parts together form
one `TeamPlan`, which the agents agree on by taking turns on `/mrs/dialogue`.
A coordinator with no LLM runs the turns and sends the agreed plan to the gate (`/mrs/proposed_plan`).
For now there is no replanning: one dialogue per mission, one plan to the gate.

## Run

```bash
colcon build --packages-up-to mrs_llm_planner   # also builds mrs_sim, fleet_checker, mrs_ethical_gate
source install/setup.zsh
ollama pull phi3.5                                                    # once; keep the Ollama app running
ros2 launch mrs_llm_planner planner.launch.py                                    # ollama, phi3.5 (planner.yaml)
ros2 launch mrs_llm_planner planner.launch.py backend:=fake                      # no LLM: agents only discuss
ros2 launch mrs_llm_planner planner.launch.py fake_script:=narrow_aisle          # scripted: APPROVE
ros2 launch mrs_llm_planner planner.launch.py fake_script:=narrow_aisle_reject   # scripted: REJECT, all hold
ros2 launch mrs_llm_planner planner.launch.py backend:=anthropic model:=<model> run_id:=exp1_s01
ros2 topic pub --once /mrs/mission std_msgs/msg/String \
  "{data: 'robot_a: go to room_e. robot_b: go to room_w.'}" \
  --qos-durability transient_local --qos-reliability reliable
```

Start it with the simulation (`sim.launch.py`) and the gate (`gate.launch.py`), using the same
`world` and `robots`.

The default LLM is phi3.5 on a local Ollama server (`backend: ollama`, `model: phi3.5`). The
planner calls Ollama's native `/api/chat` with `num_ctx` (8192): Ollama's own default context is
shorter than the prompts, and Ollama cuts an over-long prompt silently. If an answer fills the
window, the call fails with "context window full" instead; raise `num_ctx`. A first call after
Ollama starts also loads the model, which takes a few seconds. For Ollama on another machine (or
an HPC node), set `base_url`, e.g. `http://gpu-node:11434`. API keys (cloud backends only) come from the environment, from `secrets_file`, from `$MRS_SECRETS`
or from `.env` in the working directory. Never put them in `planner.yaml`. For a vLLM server on
the HPC cluster, use `backend:=openai_compatible` and set `base_url` in a params file.

## Missions and goals

`/mrs/mission` is a `std_msgs/String` in one of two forms:

```
robot_a: go to room_e. robot_b: go to room_w.
{"text": "swap rooms", "goals": {"robot_a": "room_e", "robot_b": "room_w"}}
```

Each `<robot>: go to <node>` (or JSON entry) is a goal. The robot must be on the team and the node
on the map, or the coordinator refuses the mission and logs why (`mission refused, not planning`).
Text without any `go to` has no goals: planning runs, nothing is checked.

The goals are enforced in two places:

- **Agents.** A proposal in which a robot's last task does not end at its goal is refused, and the
  agent gets its one repair call with a message like `robot_b's last task ends at aisle_mid, but its
  mission goal is room_w (end at room_w, room_w_n or room_w_s)`. If the repair misses too, the agent
  skips its turn with a message saying why (see Skipped turns below). A goal that names a room
  accepts any node in it; a goal that names another node means exactly that node. The opening
  prompt lists the goals as `Goals: robot_a -> room_e (end at room_e, room_e_n or room_e_s); ...`.
- **Coordinator.** It runs the same check before sending an agreed plan to the gate. A miss makes
  every robot hold, with cause `mission_mismatch`. A plan that ignores the mission never reaches the
  gate or the robots.

Robots without a goal are not constrained. This is a validity check like grounding, not a gate rule.

## The protocol

- **Turn order.** In every round each agent speaks once, in team order (robot_a, robot_b, …).
  Before each turn the coordinator posts a prompt that names the next speaker. The first prompt
  holds the mission and where the robots are.
- **Parts.** Each agent plans only for its own robot. The team plan is every robot's latest part,
  merged in team order; every turn shows the agents the current team plan in full. A part may
  depend on a teammate's task (`"depends_on": ["a1"]`); task ids start with the robot's letter.
- **Reply kinds.** An agent replies with exactly one JSON object:
  - `proposal`: its own robot's part. Assignments for teammates in the reply are ignored.
  - `agreement`: accepts the current team plan.
  - `discussion`: text only, e.g. "robot_b, please wait for my a1".
- **Agreement.** The team plan is agreed when every robot has a part, the merged plan is valid
  (unique task ids, existing dependencies, no cycles) and every agent has accepted it. Proposing a
  part counts as accepting the plan with it. A changed part makes a new version of the plan that
  everyone must accept again; re-proposing an unchanged part counts as accepting. The coordinator
  sends the plan to the gate as soon as it is agreed.
- **Routes and conflicts.** Every turn shows each robot's route (the shortest path of each task,
  and the capacity zones it crosses). With `conflict_hints: true` (default), the agents are also
  told where two robots cross a capacity-1 zone with no order between their tasks:
  `CONFLICT: robot_a's a1 and robot_b's b1 both cross aisle (capacity 1) with no order between them.`
  That is rule S-1 checked for the agents in advance, so the gate catches less: record the setting
  in every experiment, and turn it off to compare prompting alone with the gate.
- **What is published.** An agent merges its part into the current team plan before publishing,
  so every `DialogueTurn.plan_json` is a complete, valid `TeamPlan` (no interface change). The
  coordinator and every agent read the dialogue the same way (`parts.fold`).
- **Skipped turns, never silence.** Whatever goes wrong in an agent's turn (no usable reply after its
  repairs, an LLM timeout or error, even a bug in the node), it posts a discussion turn such as
  "I skip this turn: my language model did not answer in time. Problem: ...". Its teammates see why,
  and the next robot speaks at once.
- **The dialogue fails** when there is no agreement after `max_rounds` (3; nothing goes to the gate),
  when an agent node has crashed (nothing within `turn_timeout_s`), or when the whole cycle takes
  longer than `cycle_timeout_s` (300 s). Every cycle ends, approved or with every robot holding.
- **Fallback.** There is one dialogue per mission. A REJECT, no agreement, a failed turn or no
  verdict within `verdict_timeout_s` makes the coordinator publish a FALLBACK ValidatedPlan in which
  every robot holds where it is, plus a `planning_exhausted` EpisodeEvent (its detail says why).
- **Not in this version.** No replanning of any kind: no new dialogue after a REJECT, and none while
  robots move (blocked edges, failed tasks; the executor's own reroute-or-hold handles those). The
  messages keep their `attempt` field, always 0.
- **One transaction per mission.** The coordinator mints the `transaction_id` when the mission arrives.

## Robust with any model

- **Constrained output** (`constrained_output: true`). Every LLM call carries a JSON schema built for
  this agent and this turn. Ollama, OpenAI and OpenAI-compatible servers (vLLM) enforce it while
  generating, so the reply cannot be malformed JSON, name another robot, use a node that is not on
  the map, misname a task id, or depend on a task that does not exist. `text`, every task's
  `justification` and the part's `rationale` are required, so every agent always says why.
  What a schema cannot express (the goal, cycles, the rules) is still checked by the agent and the
  gate. If the backend refuses the schema (HTTP 4xx, e.g. an older Ollama), the agent logs a warning
  and continues in plain JSON mode. Anthropic: not enforced (prompt and checks only).
- **Repairs** (`max_repairs: 1`, `repair_temperature: 0.2`). A reply that fails a check is sent back
  with the exact problem, at a lower temperature: repairs should be careful, not creative.
- **Bounded time.** Failed turns are reported at once; `turn_timeout_s` only catches a crashed agent
  node; `cycle_timeout_s` caps the whole cycle.
- **Measured.** The coordinator records one row per planning cycle in `log_file` (outcome, cause,
  rounds, turns, skipped turns, plan versions, duration). Summarize a run with:

  ```bash
  ros2 run mrs_llm_planner summary                      # ./llm_log.sqlite, every run
  ros2 run mrs_llm_planner summary --run exp1_s01 --json
  ```

  ```
  run exp1_s01  (ollama/phi3.5)
    cycles 20: approved 13 (65%), fallback 7 [no_agreement 4, rejected 3]
    per cycle: 41.2 s, 2.4 rounds, 4.6 turns, 2.9 plan versions; skipped turns 5 in total
    LLM: 98 calls (6 repairs, 2 failed [llm_timeout 2]), 8.7 s per call, tokens ...
  ```
  (illustrative numbers)

## The rules the agents are told

`guidelines.yaml` has one plain-language text per gate rule. The agents get the texts of the
rules named in `rules` (default `S-1,P-1,P-2`, the gate's defaults). Keep `rules` equal to the
gate's `rule_*` switches. A test checks the defaults agree.

## What is recorded

`log_file` is an SQLite file that records every LLM request and response with its run, agent,
episode, transaction, round, latency and tokens. Token counts live only there, because
DialogueTurn has no token fields. `response_log.ReplayClient` can replay a run's answers for
debugging. Do not reuse one run's answers in another run of an experiment.

## Code

| Module | Contents |
|---|---|
| `llm_client.py` | OpenAI, Anthropic, OpenAI-compatible (vLLM) and fake clients. Stdlib HTTP, retries with backoff. |
| `response_log.py` | SQLite log, logging wrapper, replay. |
| `guidelines.py`, `guidelines.yaml` | Plain-language rules keyed by gate rule id. |
| `prompts.py` | System prompt (team, protocol, map, rules, formats) and the per-turn message. |
| `routes.py` | Shortest-path routes per task; conflicts in capacity-1 zones (conflict_hints). |
| `parts.py` | Folds the dialogue into parts, versions and acceptances; merges parts into the team plan. |
| `mission.py` | Parses missions into goals; checks that a plan ends each robot at its goal. |
| `turns.py` | Parses a reply, takes the robot's own part, merges and validates it, grounds it, checks its goal. |
| `agent.py` | One turn: LLM call, validation, one repair. `TranscriptTracker` for the node. |
| `protocol.py` | The coordinator's state machine and the fallback plan. |
| `settings.py` | Parameters, checked. |
| `summary.py` | `ros2 run mrs_llm_planner summary`: cycles and LLM calls of a run, from the log. |
| `agent_node.py`, `coordinator_node.py` | Thin ROS shells around the modules above. |

Run the tests without ROS (from this folder, with pydantic and pyyaml installed): `python -m pytest`.
`test_nodes.py` runs the nodes and the gate node in one process and needs the built workspace.
