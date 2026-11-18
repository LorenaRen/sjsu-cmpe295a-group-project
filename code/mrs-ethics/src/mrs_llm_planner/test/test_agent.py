from mrs_common import reasons as R
from mrs_llm_planner.agent import Agent, TranscriptTracker
from mrs_llm_planner.llm_client import FakeClient, LLMError, LLMTimeout
from mrs_llm_planner.prompts import TurnRequest
from plans import HALLUCINATED, TAKE_TURNS, agreement, proposal

REQ = TurnRequest(round=1, transcript=())


def test_good_reply(context):
    fake = FakeClient(script=[proposal(TAKE_TURNS)])
    r = Agent(context, fake, temperature=0.3).take_turn(REQ)
    assert r.ok and r.reply.kind == "proposal" and r.calls == 1 and not r.repaired
    assert fake.calls[0][0].role == "system" and r.tokens_in > 0


def test_one_repair(context):
    fake = FakeClient(script=[proposal(HALLUCINATED), agreement()])
    r = Agent(context, fake).take_turn(REQ)
    assert r.ok and r.repaired and r.calls == 2 and r.reply.kind == "agreement"
    repair = fake.calls[1]
    assert repair[-2].role == "assistant" and "unknown target 'kitchen'" in repair[-1].content


def test_malformed_twice(context):
    r = Agent(context, FakeClient(script=["nonsense"])).take_turn(REQ)
    assert not r.ok and r.reason == R.LLM_MALFORMED and r.calls == 2 and "not JSON" in r.error


def test_llm_failures_keep_their_reason(context):
    assert Agent(context, FakeClient(script=[LLMTimeout("slow")])).take_turn(REQ).reason == R.LLM_TIMEOUT
    r = Agent(context, FakeClient(script=["bad", LLMError("down")])).take_turn(REQ)
    assert r.reason == R.LLM_ERROR and r.calls == 2


def test_tracker_keeps_current_mission():
    t = TranscriptTracker()
    t.hear(transaction_id="t", round=1, kind="proposal", speaker_id="robot_b", text="late")  # ignored
    t.hear(transaction_id="t", round=1, kind="prompt", speaker_id="coordinator", text="Mission: m")
    t.hear(transaction_id="t", round=1, kind="proposal", speaker_id="robot_a", text="p", plan_json="{}")
    t.hear(transaction_id="t", round=1, kind="prompt", speaker_id="coordinator", text="robot_b, go")
    assert [e.kind for e in t.request(round=1).transcript] == ["prompt", "proposal"]
    t.hear(transaction_id="t2", round=1, kind="prompt", speaker_id="coordinator", text="Next mission")
    assert [e.text for e in t.request(round=1).transcript] == ["Next mission"]


def test_goal_miss_is_repaired_once_then_skipped(context):
    """The agent checks goals: one repair; a second miss is llm_malformed, with a skip message."""
    from mrs_llm_planner.agent import failure_text
    from mrs_llm_planner.mission import Goal
    req = TurnRequest(round=1, transcript=(), goals={"robot_a": Goal("room_e_s", ("room_e_s",))})
    fake = FakeClient(script=[proposal(TAKE_TURNS)])           # ends robot_a at room_e, both times
    r = Agent(context, fake).take_turn(req)
    assert not r.ok and r.reason == R.LLM_MALFORMED and r.calls == 2
    assert "robot_a's last task ends at room_e, but its mission goal is room_e_s" in fake.calls[1][-1].content
    text = failure_text(r)
    assert text.startswith("I skip this turn: I could not produce a valid plan. Problem: robot_a's last task")


def test_agent_merges_its_part_with_teammates_parts(world_map):
    """robot_b's agent: its part depends on robot_a's a1, which is in the transcript."""
    import json
    from mrs_llm_planner.guidelines import BASIC_RULES, guidelines_for
    from mrs_llm_planner.prompts import PlannerContext, TranscriptEntry
    from plans import A_GOES, B_AFTER_A, part
    ctx_b = PlannerContext("robot_b", ("robot_a", "robot_b"), world_map.to_prompt_dict(),
                           tuple(guidelines_for(BASIC_RULES)))
    req = TurnRequest(round=1, transcript=(TranscriptEntry("robot_a", "proposal", "", json.dumps(part(A_GOES))),))
    r = Agent(ctx_b, FakeClient(script=[proposal(part(B_AFTER_A))])).take_turn(req)
    assert r.ok and [a.robot_id for a in r.reply.plan.assignments] == ["robot_a", "robot_b"]
    # without robot_a's part, the dependency on a1 has nothing to point at: repair, then skip
    r = Agent(ctx_b, FakeClient(script=[proposal(part(B_AFTER_A))])).take_turn(TurnRequest(round=1, transcript=()))
    assert not r.ok and "depends on unknown task" in r.error


def test_repairs_are_configurable_and_cooler(context):
    fake = FakeClient(script=["bad", "still bad", agreement()])
    r = Agent(context, fake, temperature=0.7, max_repairs=2, repair_temperature=0.1).take_turn(REQ)
    assert r.ok and r.calls == 3 and fake.temperatures == [0.7, 0.1, 0.1]
    r = Agent(context, FakeClient(script=["bad"]), max_repairs=0).take_turn(REQ)
    assert not r.ok and r.calls == 1


def test_unconstrained_sends_no_schema(context):
    fake = FakeClient(script=[agreement()])
    Agent(context, fake, constrained=False).take_turn(REQ)
    assert fake.schemas == [None]


def test_failure_texts():
    from mrs_llm_planner.agent import failure_text
    assert failure_text(R.LLM_TIMEOUT, "no answer within 60 s") == \
        "I skip this turn: my language model did not answer in time. Problem: no answer within 60 s"
    assert failure_text("internal_error", "KeyError: x").startswith("I skip this turn: something went wrong on my side")


def test_backend_that_refuses_the_schema_turns_it_off(context):
    """An older Ollama may reject the schema: the turn goes on in plain JSON mode, and so do later ones."""
    class Picky(FakeClient):
        def complete(self, messages, *, temperature, json_mode=True, schema=None):
            if schema is not None:
                self.schemas.append(schema)
                raise LLMError("HTTP 400: invalid format")
            return super().complete(messages, temperature=temperature, json_mode=json_mode)
    fake = Picky(script=[agreement()])
    agent = Agent(context, fake)
    r = agent.take_turn(REQ)
    assert r.ok and r.calls == 2 and not agent.constrained and "invalid format" in agent.schema_off_reason
    r = agent.take_turn(REQ)
    assert r.ok and r.calls == 1
