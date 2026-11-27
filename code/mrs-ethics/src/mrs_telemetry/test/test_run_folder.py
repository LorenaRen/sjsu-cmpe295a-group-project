import json
import subprocess
from datetime import datetime

import pytest

from mrs_telemetry import run_folder as RF


def test_run_dir_for(tmp_path):
    d = RF.run_dir_for(tmp_path, "S5 keep clear", "seed 03")
    assert d == tmp_path / "S5_keep_clear" / "seed_03"
    assert RF.run_dir_for(tmp_path).parent.name == "dev"
    assert RF.new_run_id(datetime(2026, 11, 17, 19, 30, 5), suffix=False) == "20261117-193005"
    ids = {RF.new_run_id(datetime(2026, 11, 17, 19, 30, 5)) for _ in range(20)}
    assert all(i.startswith("20261117-193005-") and len(i) == 20 for i in ids) and len(ids) > 1
    with pytest.raises(ValueError):
        RF.safe_name(" / ")


def test_update_run_json_keeps_other_sections(tmp_path):
    run = tmp_path / "exp" / "r1"
    RF.update_run_json(run, "episode", {"world": "narrow_aisle"})
    info = RF.update_run_json(run, "recording", {"robots": ["robot_a"]})
    assert info["episode"] == {"world": "narrow_aisle"} and info["recording"]["robots"] == ["robot_a"]
    assert info["run_id"] == "r1" and info["experiment"] == "exp" and info["schema"] == RF.SCHEMA
    created = info["created"]
    info = RF.update_run_json(run, "episode", {"world": "aisle_bypass"})
    assert info["episode"] == {"world": "aisle_bypass"} and info["created"] == created
    assert json.loads((run / "run.json").read_text()) == info
    assert not (run / "run.json.tmp").exists()


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def test_git_info(tmp_path):
    assert RF.git_info(tmp_path) == {}
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    (tmp_path / "f.txt").write_text("1")
    _git(tmp_path, "add", "f.txt")
    _git(tmp_path, "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-q", "-m", "y")
    info = RF.git_info(tmp_path)
    assert len(info["commit"]) == 40 and info["dirty"] is False
    (tmp_path / "f.txt").write_text("2")
    info = RF.git_info(tmp_path)
    assert info["dirty"] is True and info["changed_files"] == ["f.txt"]
