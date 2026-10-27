"""Static checks of the .msg files, runnable before anything is built.

Catches the mistakes that otherwise only show up as a cryptic colcon/rosidl error:
bad field names, unknown types, comments glued onto string constants, and helper code
in mrs_common.convert that refers to a field the message does not have.
"""
import re
from pathlib import Path

import pytest

MSG_DIR = Path(__file__).resolve().parents[2] / "mrs_msgs" / "msg"
# No module-level pytest.skip(): see the note in test_roundtrip.py.
pytestmark = pytest.mark.skipif(not MSG_DIR.is_dir(), reason="mrs_msgs source not found next to mrs_common")

PRIMITIVES = {"bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16", "int32",
              "uint32", "int64", "uint64", "string", "wstring"}
EXTERNAL = {"builtin_interfaces/Time", "geometry_msgs/Vector3"}
LOCAL = {p.stem for p in MSG_DIR.glob("*.msg")}
FIELD_NAME = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
CONST_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

CMAKE_FILE = MSG_DIR.parent / "CMakeLists.txt"
CMAKE = CMAKE_FILE.read_text() if CMAKE_FILE.is_file() else ""


def parse(path: Path):
    fields, consts = {}, {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        typ, rest = line.split(None, 1)
        if "=" in rest.split("#")[0] and not rest.lstrip().startswith("#"):
            name, value = rest.split("=", 1)
            consts[name.strip()] = (typ, value)
        else:
            name = rest.split("#")[0].strip()
            fields[name] = typ
    return fields, consts


ALL = {p.stem: parse(p) for p in sorted(MSG_DIR.glob("*.msg"))}


def test_expected_messages_exist():
    expected = {"PerceivedEntity", "WorldState", "TeamState", "DialogueTurn", "ProposedPlan", "EthicalVerdict",
                "ValidatedPlan", "TaskStatus", "SafetyOverride", "EpisodeEvent"}
    assert expected <= LOCAL


@pytest.mark.parametrize("name", sorted(LOCAL))
def test_listed_in_cmake(name):
    assert f'"msg/{name}.msg"' in CMAKE


@pytest.mark.parametrize("name", sorted(ALL))
def test_fields_and_types(name):
    fields, consts = ALL[name]
    for fname, typ in fields.items():
        assert FIELD_NAME.match(fname), f"{name}.{fname}: field names must be lower_snake_case"
        base = re.sub(r"(\[\d*\]|\[<=\d+\])$", "", typ)
        assert base in PRIMITIVES | EXTERNAL | LOCAL, f"{name}.{fname}: unknown type '{typ}'"
    for cname, (typ, value) in consts.items():
        assert CONST_NAME.match(cname), f"{name}.{cname}: constants must be UPPER_CASE"
        assert "#" not in value, f"{name}.{cname}: a comment after a string constant becomes part of its value"


@pytest.mark.parametrize("name", sorted(ALL))
def test_no_equals_sign_in_field_comments(name):
    """'=' anywhere on a field line can make rosidl read the line as a constant."""
    for raw in (MSG_DIR / f"{name}.msg").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = line.split("#", 1)[0]
        if "=" not in head:
            assert "=" not in line, f"{name}.msg: avoid '=' in a field comment: {line!r}"


@pytest.mark.parametrize("name", ["DialogueTurn", "ProposedPlan", "EthicalVerdict", "ValidatedPlan",
                                  "TaskStatus", "EpisodeEvent"])
def test_decision_messages_carry_ids_and_stamp(name):
    fields, _ = ALL[name]
    assert "episode_id" in fields and fields.get("stamp") == "builtin_interfaces/Time"
    if name not in ("DialogueTurn", "EpisodeEvent"):
        assert "transaction_id" in fields


def test_every_event_kind_is_accepted_by_make_episode_event():
    """A KIND_* constant added to EpisodeEvent.msg must also be added to EVENT_KINDS in convert.py."""
    src = (Path(__file__).resolve().parents[1] / "mrs_common" / "convert.py").read_text()
    kinds = re.search(r"EVENT_KINDS = \((.*?)\)\n", src, re.S).group(1)
    for const in ALL["EpisodeEvent"][1]:
        assert f"EpisodeEvent.{const}" in kinds, f"convert.EVENT_KINDS is missing EpisodeEvent.{const}"


def test_team_state_carries_blocked_edges():
    assert ALL["TeamState"][0].get("blocked_edges") == "string[]"


def test_convert_helpers_match_message_fields():
    """Every keyword passed to a message constructor in convert.py must be a real field."""
    src = (Path(__file__).resolve().parents[1] / "mrs_common" / "convert.py").read_text()
    for msg_name in ("ProposedPlan", "ValidatedPlan", "EthicalVerdict", "DialogueTurn", "TaskStatus",
                     "EpisodeEvent"):
        for call in re.finditer(rf"\b{msg_name}\((.*?)\)\n", src, re.S):
            kwargs = set(re.findall(r"(\w+)=", call.group(1)))
            unknown = kwargs - set(ALL[msg_name][0])
            assert not unknown, f"convert.py builds {msg_name} with unknown field(s) {unknown}"
