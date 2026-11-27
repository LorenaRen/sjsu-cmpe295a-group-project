import json

import pytest

from mrs_llm_planner.settings import PARAMETERS, SettingsError, load_map, load_settings


def test_defaults():
    s = load_settings({}, agent=False)
    assert s.team == ("robot_a", "robot_b") and s.rules == ("S-1", "P-1", "P-2")
    assert s.llm.model_name == "fake" and s.max_rounds == 3
    wm, spawns = load_map(s)
    assert spawns == {"robot_a": "room_w_s", "robot_b": "room_e_n"}


@pytest.mark.parametrize("values", [
    {"robot_id": "robot_z"},
    {"robot_id": "robot_a", "rules": "S-1,Q-7"},
    {"robot_id": "robot_a", "backend": "openai"},                     # no model
    {"robot_id": "robot_a", "temperature": 3.0},
    {"robot_id": "robot_a", "turn_timeout_s": 100.0},                 # < 2 x llm_timeout_s
    {"robot_id": "robot_a", "robots": 0},
])
def test_bad_values(values):
    with pytest.raises(SettingsError):
        load_settings(values, agent=True)


def test_fake_script_file(tmp_path):
    f = tmp_path / "script.json"
    f.write_text(json.dumps({"robot_a": [{"kind": "discussion", "text": "hi"}, "raw text"]}))
    s = load_settings({"robot_id": "robot_a", "fake_script_file": str(f)}, agent=True)
    assert s.fake_script == ['{"kind": "discussion", "text": "hi"}', "raw text"]
    assert load_settings({"robot_id": "robot_b", "fake_script_file": str(f)}, agent=True).fake_script is None
    f.write_text(json.dumps({"robot_q": ["x"]}))
    with pytest.raises(SettingsError):
        load_settings({"robot_id": "robot_a", "fake_script_file": str(f)}, agent=True)


def test_shipped_fake_script_is_valid(world_map):
    from pathlib import Path
    from mrs_llm_planner.turns import parse_reply
    paths = sorted((Path(__file__).resolve().parents[1] / "config").glob("fake_*.json"))
    assert paths
    for path in paths:
        for r in ("robot_a", "robot_b"):
            s = load_settings({"robot_id": r, "fake_script_file": str(path)}, agent=True)
            others = {"robot_a": {"robot_id": "robot_a", "tasks": [{"task_id": "a1", "action": "navigate",
                      "target": "room_e", "priority": 1, "justification": "", "depends_on": []}]}}
            for raw in s.fake_script:   # robot_b's part may depend on robot_a's a1
                parse_reply(raw, robot_id=r, team=s.team, graph_nodes=tuple(world_map.nodes), others=others)


def test_config_file_names_only_known_parameters():
    import yaml
    from pathlib import Path
    data = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "planner.yaml").read_text())
    common = data["/**"]["ros__parameters"]
    for node in ("mrs_coordinator", "/**/mrs_agent"):
        params = {**common, **data[node]["ros__parameters"]}
        assert set(params) <= set(PARAMETERS)
        s = load_settings({**params, "robot_id": "robot_a"}, agent=True)
        assert s.llm.model_name == "ollama/phi3.5" and s.llm.num_ctx == 8192



def test_launch_overrides_are_not_set_per_node_in_the_config_file():
    # planner.launch.py's overrides arrive in the "/**" section; a node section of planner.yaml that
    # sets the same parameter wins over them (episode_id once never reached the coordinator).
    import yaml
    from pathlib import Path
    data = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "planner.yaml").read_text())
    overridden = {"world", "robots", "use_sim_time", "backend", "model", "run_id", "log_file", "episode_id"}
    for section, body in data.items():
        if section != "/**":
            assert not overridden & set(body["ros__parameters"]), section
