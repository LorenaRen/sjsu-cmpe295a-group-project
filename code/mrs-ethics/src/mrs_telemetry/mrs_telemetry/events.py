"""Decision events: one JSON line per message on a /mrs decision topic (Module 7). Pure Python.

The bag has everything; events.jsonl is the index of what was decided, small enough to read
by eye, grep, or load into pandas, and what the dashboard (Module 8) jumps to in the bag by `t`.

Every line has:
  t               simulation time when the event was received [s] (the node clock; None before /clock)
  stamp           the message's own stamp [s], when it has one
  wall            wall-clock time (UTC, ISO 8601)
  type            mission | dialogue | proposed_plan | verdict | validated_plan | task_status |
                  episode_event | safety_override | telemetry_start | telemetry_end
  summary         one human-readable line
plus the fields of that type (ids, verdict, rule ids, plan summary, ...).

Messages are read by attribute name only, so the functions work on real mrs_msgs messages and on
plain test objects alike.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mrs_common import names as N

TEXT_MAX = 1500   # longer dialogue text and rationales are cut here; the bag has them whole


def stamp_s(stamp) -> Optional[float]:
    """builtin_interfaces/Time -> seconds; None for a missing or zero stamp."""
    if stamp is None:
        return None
    s = getattr(stamp, "sec", 0) + getattr(stamp, "nanosec", 0) * 1e-9
    return round(s, 6) if s else None


def _cut(text: str, n: int = TEXT_MAX) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _task(t: dict) -> str:
    head = f"{t.get('task_id')} {t.get('action')} {t.get('target')}"
    deps = ",".join(t.get("depends_on") or [])
    return f"{head} (after {deps})" if deps else head


def plan_summary(plan_json: str) -> str:
    """'robot_a: a1 navigate room_e | robot_b: b1 navigate room_w (after a1)'; tolerant of bad JSON."""
    if not plan_json:
        return ""
    try:
        data = json.loads(plan_json)
        parts = [f"{a.get('robot_id')}: " + ", ".join(_task(t) for t in a.get("tasks", []))
                 for a in data.get("assignments", [])]
    except (ValueError, AttributeError, TypeError):
        return "(unreadable plan_json)"
    return " | ".join(parts) or "(no assignments)"


def _ids(msg) -> dict:
    out = {}
    for name in ("episode_id", "transaction_id", "attempt"):
        if hasattr(msg, name):
            out[name] = getattr(msg, name)
    return out


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text) if text else {}
    except ValueError:
        return text


# ---- one function per topic: msg -> (type, fields, summary) ------------------------------------
def _mission(msg):
    return "mission", {"text": _cut(msg.data)}, f"mission: {_cut(msg.data, 200)}"


def _dialogue(msg):
    f = {**_ids(msg), "round": msg.round, "kind": msg.kind, "speaker": msg.speaker_id}
    if msg.next_speaker:
        f["next_speaker"] = msg.next_speaker
    f["text"] = _cut(msg.text)
    if msg.plan_json:
        f["plan"] = plan_summary(msg.plan_json)
    if msg.kind == "prompt":
        summary = f"round {msg.round}: {msg.next_speaker}'s turn"
    else:
        summary = f"round {msg.round}: {msg.speaker_id} {msg.kind}" + (f": {f['plan']}" if "plan" in f else
                                                                     f": {_cut(msg.text, 120)}" if msg.text else "")
    return "dialogue", f, summary


def _proposed(msg):
    f = {**_ids(msg), "model": msg.model_name, "latency_ms": round(msg.latency_ms, 1),
         "tokens_in": msg.tokens_in, "tokens_out": msg.tokens_out, "plan": plan_summary(msg.plan_json),
         "dialogue_digest": _cut(msg.dialogue_digest, 500)}
    return "proposed_plan", f, f"plan to the gate: {f['plan']}"


def _verdict(msg):
    constraints = _json_or_text(msg.constraints_json)
    kinds = [c.get("kind") for c in constraints if isinstance(c, dict)] if isinstance(constraints, list) else []
    f = {**_ids(msg), "verdict": msg.verdict, "rule_ids": list(msg.rule_ids), "risk_score": round(msg.risk_score, 3),
         "rationale": _cut(msg.rationale), "constraints": kinds, "latency_ms": round(msg.latency_ms, 2)}
    rules = f" ({', '.join(f['rule_ids'])})" if f["rule_ids"] else ""
    return "verdict", f, f"{msg.verdict}{rules}"


def _validated(msg):
    f = {**_ids(msg), "source_verdict": msg.source_verdict, "plan": plan_summary(msg.plan_json)}
    return "validated_plan", f, f"plan to the robots ({msg.source_verdict}): {f['plan']}"


def _task_status(msg):
    f = {**_ids(msg), "robot": msg.robot_id, "task": msg.task_id, "state": msg.state}
    if msg.waypoint:
        f["waypoint"] = msg.waypoint
    if msg.detail:
        f["detail"] = _cut(msg.detail, 300)
    where = f" at {msg.waypoint}" if msg.waypoint else ""
    return "task_status", f, f"{msg.robot_id} {msg.task_id} {msg.state}{where}"


def _episode_event(msg):
    f = {**_ids(msg), "kind": msg.kind, "detail": _json_or_text(msg.detail_json)}
    if msg.robot_id:
        f["robot"] = msg.robot_id
    reason = f["detail"].get("reason") if isinstance(f["detail"], dict) else None
    who = f" {msg.robot_id}" if msg.robot_id else ""
    return "episode_event", f, f"{msg.kind}{who}" + (f": {reason}" if reason else "")


def _vec(v) -> list[float]:
    return [round(v.x, 3), round(v.y, 3)]


def _safety_override(msg):
    f = {**_ids(msg), "robot": msg.robot_id, "min_distance": round(msg.min_distance, 3),
         "active_constraints": list(msg.active_constraints), "u_nominal": _vec(msg.u_nominal),
         "u_safe": _vec(msg.u_safe)}
    return "safety_override", f, f"CBF override {msg.robot_id}, min distance {f['min_distance']} m"


HANDLERS: dict[str, Callable] = {
    N.MISSION: _mission, N.DIALOGUE: _dialogue, N.PROPOSED_PLAN: _proposed, N.VERDICT: _verdict,
    N.VALIDATED_PLAN: _validated, N.TASK_STATUS: _task_status, N.EPISODE_EVENT: _episode_event,
    N.SAFETY_OVERRIDE: _safety_override,
}
EVENT_TOPICS = tuple(HANDLERS)


def wall_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def make_event(topic: str, msg, *, t: Optional[float], wall: Optional[str] = None) -> dict:
    kind, fields, summary = HANDLERS[topic](msg)
    return {"t": t, "stamp": stamp_s(getattr(msg, "stamp", None)), "wall": wall or wall_now(),
            "type": kind, "summary": summary, **fields}


class Throttle:
    """At most one safety_override event per robot per `interval` seconds of sim time: the CBF
    filter can override at its full rate. The next event counts what was skipped (`skipped`);
    the bag keeps every override."""

    def __init__(self, interval: float = 1.0):
        self.interval = interval
        self.last: dict[str, float] = {}
        self.skipped: dict[str, int] = {}

    def allow(self, key: str, t: Optional[float]) -> tuple[bool, int]:
        """(write this one?, how many were skipped before it)."""
        if t is None or key not in self.last or t - self.last[key] >= self.interval or t < self.last[key]:
            self.last[key] = t if t is not None else self.last.get(key, 0.0)
            return True, self.skipped.pop(key, 0)
        self.skipped[key] = self.skipped.get(key, 0) + 1
        return False, 0


class EventLog:
    """Appends events to events.jsonl, one line each, flushed at once: a crash loses nothing
    written before it."""

    def __init__(self, path: str | Path, *, override_interval: float = 1.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", encoding="utf-8")
        self.counts: dict[str, int] = {}
        self.throttle = Throttle(override_interval)

    def write(self, event: dict) -> None:
        self._f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self._f.flush()
        self.counts[event["type"]] = self.counts.get(event["type"], 0) + 1

    def record(self, topic: str, msg, *, t: Optional[float]) -> Optional[dict]:
        """Turn a message into an event and write it (None if throttled)."""
        if topic == N.SAFETY_OVERRIDE:
            ok, skipped = self.throttle.allow(msg.robot_id, t)
            if not ok:
                return None
            event = make_event(topic, msg, t=t)
            if skipped:
                event["skipped"] = skipped
        else:
            event = make_event(topic, msg, t=t)
        self.write(event)
        return event

    def note(self, kind: str, t: Optional[float], summary: str, **fields) -> dict:
        event = {"t": t, "stamp": None, "wall": wall_now(), "type": kind, "summary": summary, **fields}
        self.write(event)
        return event

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()


def read_events(path: str | Path) -> tuple[list[dict], list[str]]:
    """(events, problems): a line that is not JSON is a problem, not a crash."""
    events, problems = [], []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError as e:
            problems.append(f"events.jsonl line {i} is not JSON: {e}")
    return events, problems
