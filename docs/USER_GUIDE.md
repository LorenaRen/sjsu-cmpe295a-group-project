# MRS Ethics — Team User Guide

How to set up, build, test and use the modules that exist so far. Update this guide whenever
a module is finished: add a row to the status table and a section under "Using the modules".

Module-specific design notes live next to this file (e.g. `module1_interfaces.md`). The overall
plan is the team's *End-to-End Implementation Guide*.

---

## 1. What exists today

| Module | Package(s) | Status | What you can do with it |
|---|---|---|---|
| 0 Repository and containers | — | not started | (conda env set up by hand, see §2) |
| 1 Workspace and interfaces | `mrs_msgs`, `mrs_common` | **done** — 89 tests pass on macOS | Message types, plan format, topic names, message builders, dev nodes |
| 2 Simulation world | `mrs_sim` | next | — |
| 3 World state | `mrs_world_state` | — | — |
| 4 LLM planner | `mrs_llm_planner` | — | — |
| 5 Ethical gatekeeper | `mrs_ethical_gate` | — | — |
| 6 Controller + CBF | `mrs_control_cbf` | — | — |
| 7 Recording | `mrs_telemetry` | — | — |

---

## 2. One-time setup (macOS, Apple Silicon)

You need Miniconda and a conda environment with ROS 2 Jazzy from RoboStack (the one used for
the ROS tutorials). The team environment is called `mrs-ethics`.

```bash
conda activate mrs-ethics
ros2 --help                           # must print help

# Check what Module 1 needs; install only what is missing
python -c "import pydantic; print(pydantic.VERSION)"     # need 2.x
python -c "import setuptools; print(setuptools.__version__)"  # need < 80 for --symlink-install
pytest --version
which cmake clang

conda install -c conda-forge "pydantic>=2" pytest "setuptools<80" compilers cmake make pkg-config
```

When Module 0 is done, these move into the team's `environment.yml` and nobody installs by hand.

Get the code: clone the team repo (or unzip the module drop) so you have:

```
mrs-ethics/                 <- the workspace root: run colcon here
  src/
    mrs_msgs/               message definitions (.msg)
    mrs_common/             shared Python code
  docs/
  build/ install/ log/      created by colcon; never commit these (.gitignore)
```

---

## 3. Daily workflow

Every new terminal:

```bash
conda activate mrs-ethics
cd ~/path/to/mrs-ethics
source install/setup.zsh          # after the first build; setup.bash in Docker/Linux
```

| You changed... | Then run |
|---|---|
| a `.msg` file, a `setup.py`, `package.xml` or `CMakeLists.txt` | `colcon build` then `source install/setup.zsh` |
| Python code in a package | nothing to rebuild for `pytest`; `colcon build` again before `ros2 run` (plain build copies files) |
| nothing, just pulled | `colcon build` then `source install/setup.zsh` |

Build only what you need: `colcon build --packages-up-to mrs_common`.

`colcon build --symlink-install` (no rebuild after Python edits) also works once setuptools is
below 80; see Troubleshooting.

### Running tests

**On the Macs, always run tests with `pytest`, not `colcon test`** (macOS strips the library
path that `colcon test` needs; details in Troubleshooting). `colcon test` is for Docker/CI.

```bash
cd src/mrs_common
pytest -q            # all tests: expect 89 passed
pytest -q -rs        # also print why anything was skipped
pytest -q test/test_plan_schema.py          # one file
pytest -q -k round_trip                     # tests whose name matches
```

Skipped tests mean the terminal was not sourced or the workspace is not built.

---

## 4. Using the modules

### 4.1 Module 1 — interfaces (`mrs_msgs`, `mrs_common`)

Every node in every module uses this. The rule: **never type a topic name, never fill a decision
message by hand, never parse `plan_json` without the schema.**

#### The plan format (`TeamPlan`)

```python
from mrs_common import TeamPlan, Assignment, Task, Action, plan_to_json, plan_from_json

plan = TeamPlan(assignments=[
    Assignment(robot_id="robot_a", tasks=[
        Task(task_id="a1", action=Action.CARRY, target="room_3", priority=1,
             justification="robot_a carries the medical kit"),
    ]),
    Assignment(robot_id="robot_b", tasks=[
        Task(task_id="b1", action=Action.WAIT, target="corridor_2", priority=2,
             depends_on=["a1"], justification="let robot_a through the doorway first"),
    ]),
])

text = plan_to_json(plan)            # what goes into every plan_json field
same = plan_from_json(text)          # raises pydantic.ValidationError if invalid
```

Actions: `navigate`, `wait`, `hold_position`, `assist`, `carry`. Priority 1 = highest.
`depends_on` lists task ids that must finish first. Validation rejects unknown fields,
duplicate task or robot ids, unknown or circular `depends_on`, priorities outside 1–10.

Useful helpers:

```python
from mrs_common import check_references, hold_all_plan, team_plan_json_schema

problems = check_references(plan, robot_ids=["robot_a", "robot_b"],
                            graph_nodes=["room_3", "corridor_2"])   # [] = all targets exist
fallback = hold_all_plan(["robot_a", "robot_b"],
                         {"robot_a": "corridor_1", "robot_b": "room_2"}, reason="attempts exhausted")
schema = team_plan_json_schema()     # JSON Schema dict for LLM structured output (Module 4)
```

#### Validating LLM output (Module 4)

```python
from pydantic import ValidationError
from mrs_common import plan_from_json

def parse_llm_answer(raw: str):
    try:
        return plan_from_json(raw), None
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return None, f"{where}: {first['msg']}"     # send this back to the LLM once
```

#### Constraints (gatekeeper → planner)

```python
from mrs_common import Constraint, constraints_to_json, constraints_from_json

c = Constraint(kind="replace_target", rule_id="S1_exit_clearance", robot_id="robot_b",
               task_id="b1", value="corridor_2", text="Park at corridor_2 instead of exit_A.")
```

Kinds: `forbid_node`, `avoid_node`, `replace_target`, `order_before`, `reassign`, `custom`.
`text` is required: it is what the planner appends to the LLM prompt.

#### Topic names, frames, QoS

```python
from mrs_common import names as N
from mrs_common.qos import qos_for

N.robot_topic("robot_a", N.SCAN)        # "/robot_a/scan"
N.robot_topic("robot_b", N.CMD_VEL)     # "/robot_b/cmd_vel"
N.PROPOSED_PLAN                         # "/mrs/proposed_plan"  (also VERDICT, VALIDATED_PLAN, ...)
N.base_frame("robot_a")                 # "robot_a/base_link"
N.robot_ids(3)                          # ["robot_a", "robot_b", "robot_c"]

pub = node.create_publisher(MsgType, topic, qos_for(topic))   # publisher AND subscriber use qos_for
```

`ros2 run mrs_common check_interfaces --robots 2` prints every canonical topic and its type.

#### Building and reading messages (needs the built workspace)

```python
from mrs_common.convert import (make_proposed_plan, plan_from_msg, make_verdict,
                                constraints_from_verdict, make_validated_plan,
                                make_dialogue_turn, make_task_status, make_episode_event,
                                world_state_to_summary)
from mrs_common.ids import new_episode_id

ep = new_episode_id("S1 exit blocking", seed=3)       # "s1-exit-blocking-s3-<time>-<hex>"

# planner
msg = make_proposed_plan(plan, episode_id=ep, attempt=0, model_name="anthropic/...",
                         latency_ms=2300, tokens_in=900, tokens_out=150, node=self)

# gatekeeper: reuse the transaction_id of the plan it judges
v = make_verdict(transaction_id=msg.transaction_id, episode_id=ep, attempt=0, verdict="MODIFY",
                 rule_ids=["S1_exit_clearance"], risk_score=0.6,
                 rationale="robot_b would block exit_A", constraints=[c], node=self)
ok = make_validated_plan(plan, transaction_id=msg.transaction_id, episode_id=ep, attempt=0,
                         source_verdict="MODIFY", node=self)

# planner, on receiving the verdict
constraints = constraints_from_verdict(v)
plan_again  = plan_from_msg(ok)                 # works for ProposedPlan, ValidatedPlan, DialogueTurn

# executor / runner
make_task_status(transaction_id=msg.transaction_id, episode_id=ep, robot_id="robot_a",
                 task_id="a1", state="done", node=self)        # started|reached|done|failed|timed_out
make_episode_event(episode_id=ep, kind="violation", robot_id="robot_b",
                   detail={"rule": "S1"}, node=self)          # start|end|violation|timeout|planning_exhausted

# agent: WorldState -> compact dict for the prompt
summary = world_state_to_summary(world_state_msg)
```

Pass `node=self` so stamps use the node clock (simulation time when `use_sim_time:=true`).
Builders reject unknown verdicts, task states and event kinds.

#### Messages and topics

| Message | Topic | Published by |
|---|---|---|
| `WorldState` (+ `PerceivedEntity`) | `/<robot>/world_state` | world state node (Module 3) |
| `TeamState` | `/mrs/team_state` | world state node |
| `DialogueTurn` | `/mrs/dialogue` | each LLM agent (Module 4) |
| `ProposedPlan` | `/mrs/proposed_plan` | planner |
| `EthicalVerdict` | `/mrs/verdict` | gatekeeper (Module 5) |
| `ValidatedPlan` | `/mrs/validated_plan` | gatekeeper |
| `TaskStatus` | `/mrs/task_status` | executor (Module 6) |
| `SafetyOverride` | `/mrs/safety_override` | CBF filter (Module 6) |
| `EpisodeEvent` | `/mrs/episode_event` | runner and monitors |

`ros2 interface show mrs_msgs/msg/<Name>` prints the fields and their comments.

#### Command-line tools and dev nodes

| Command | What it does |
|---|---|
| `ros2 run mrs_common check_interfaces [--robots N]` | Imports all message types, checks the sample plans, lists topics |
| `ros2 run mrs_common export_plan_schema [-o file.json]` | Writes the TeamPlan JSON Schema |
| `ros2 run mrs_common sample_plan_pub` | **Dev node.** Publishes a sample plan every 2 s (stands in for the planner) |
| `ros2 run mrs_common plan_echo` | **Dev node.** Prints each plan on `/mrs/proposed_plan` and whether it is valid |

`sample_plan_pub` parameters: `sample` (file name, below) and `period` (seconds):

```bash
ros2 run mrs_common sample_plan_pub --ros-args -p sample:=exit_blocking.json -p period:=5.0
```

Sample plans (`mrs_common/samples/`, load with `mrs_common.load_sample(name)`):

| File | Valid? | Use it for |
|---|---|---|
| `valid_two_robots.json` | yes | normal case |
| `exit_blocking.json` | schema-valid, **unsafe** (robot_b parks at exit_A) | first gatekeeper test (Module 5) |
| `invalid_duplicate_task.json` | no | error handling |
| `invalid_unknown_field.json` | no | error handling |
| `invalid_bad_action.json` | no | error handling |

Dev nodes are run by hand; the real system never launches them. Recording for audit and analysis
is Module 7 (`mrs_telemetry`), not a dev node.

---

## 5. Adding your own package (Modules 2–8)

1. Create it in `src/` as **ament_python** (only message packages are CMake):

   ```bash
   cd src && ros2 pkg create <name> --build-type ament_python --dependencies rclpy mrs_msgs mrs_common
   ```

2. In its `setup.py`, declare pytest the modern way (otherwise `colcon test` runs 0 tests):

   ```python
   extras_require={"test": ["pytest"]},     # not tests_require
   ```

3. Copy `src/mrs_common/pytest.ini` into the new package (lets `pytest` find the package before building).
4. One node per file; dev-only nodes go in a `dev_nodes/` folder.
5. Never block a ROS callback with an LLM call; run it in a worker thread (Module 4).
6. Tests that need ROS must **not** skip at module level (`pytest.skip(...)` / `importorskip` at the
   top of a file): ROS's `launch_testing` pytest plugin turns that into "skip the whole folder".
   Use this pattern instead (see `test_roundtrip.py`):

   ```python
   try:
       from mrs_msgs.msg import ProposedPlan
       ROS_ERROR = None
   except ImportError as e:
       ROS_ERROR = str(e)

   pytestmark = pytest.mark.skipif(ROS_ERROR is not None, reason=f"needs the built workspace: {ROS_ERROR}")
   ```

7. Add a row to the status table at the top of this guide and a section under "Using the modules".

---

## 6. Changing the interfaces

Interfaces are agreed in week 1 and frozen in week 3 (tag `interfaces-v1`).

- Before the freeze: open a PR; the owner of each affected message reviews it.
- After the freeze: a change to `src/mrs_msgs` needs all four members to approve.
- A change in meaning of a `TeamPlan` field bumps `SCHEMA_VERSION` in `plan_schema.py`.
- After editing a `.msg` file: add it to `CMakeLists.txt` if new, run `pytest test/test_msg_files.py`
  (catches the usual mistakes before a long build), then `colcon build`.
- In `.msg` files, never put a comment after a string constant and never use `=` in a comment:
  both are read as part of a constant.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ros2: command not found` | conda env not active | `conda activate mrs-ethics` |
| `Package 'mrs_common' not found` | terminal not sourced | `source install/setup.zsh` |
| `ModuleNotFoundError: No module named 'mrs_common'` in pytest | old copy without `pytest.ini` | add `pytest.ini` to the package |
| pytest: `collected 0 items / 1 skipped` | a test file skips at module level | use the `skipif` pattern (§5) |
| Tests skipped: "needs the built and sourced workspace" | not built or not sourced | `colcon build`, `source install/setup.zsh` |
| `error: option --editable not recognized` | setuptools ≥ 80 with `--symlink-install` | `conda install -c conda-forge "setuptools<80"`, or plain `colcon build`; then `rm -rf build/<pkg> install/<pkg>` |
| `colcon test`: `Ran 0 tests`, exit code 5 | `setup.py` uses `tests_require` | use `extras_require={"test": ["pytest"]}`, rebuild |
| `Could not load library libmrs_msgs__rosidl_typesupport_...dylib` / `type support not from this implementation` | macOS strips `DYLD_LIBRARY_PATH` under `colcon test` | run `pytest` from a sourced terminal instead |
| Subscriber never receives anything | QoS mismatch or wrong topic string | both sides use `qos_for(topic)` and names from `mrs_common.names`; check `ros2 topic info -v <topic>` |
| pydantic errors on import | pydantic 1.x installed | `conda install -c conda-forge "pydantic>=2"` |
| Warnings about `install_name_tool` / code signature during build | normal on Apple Silicon conda | ignore |

Still stuck: run `ros2 run mrs_common check_interfaces` and `pytest -q -rs`, and paste both outputs.
