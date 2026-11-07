import json
import re
from pathlib import Path

import pandas as pd
import pytest
import yaml

from qs import db
from qs.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[1]
CTL = "ctl"
HUMAN = "atharva"
TAG = re.compile(r"(h\d+[\w.]*|arxiv-[\d-]+|ssrn-\d+) round \d+")


class FakeAgent:
    agent_id = "orch-id"

    def __init__(self):
        self.calls, self.n = [], 0

    def post(self, room_id, content):
        self.calls.append(("post", room_id, content))

    def mention(self, room_id, body, names=(), roles=(), thread_id=None):
        self.calls.append(("mention", room_id, body))

    def create_room(self, template_yaml, inputs):
        self.n += 1
        self.calls.append(("create_room", inputs))
        return {"room_id": f"room-{self.n}", "room_name": inputs["room"]}

    def link(self, child_room, parent_room, label):
        self.calls.append(("link", child_room, parent_room, label))

    def archive(self, room_id):
        self.calls.append(("archive", room_id))


class StubRunner:
    def __init__(self):
        self.verdicts, self.stop, self.trials = [], None, []

    def run_trial(self, con, hid, window="research", gate="real"):
        v = self.verdicts.pop(0)
        self.trials.append((hid, window, gate))
        daily = pd.Series([1.0, -2.0, 3.0], index=pd.to_datetime(["2023-01-02", "2023-01-03", "2023-02-01"]))
        db.record_trial(con, hid, window, {}, "s", "e", "g", gate == "demo", daily, {}, v, v["passed"])
        return v

    def hard_stop(self, con, hid, cfg):
        return self.stop

    def check_patch(self, parent, child):
        pass


def verdict(passed):
    return {"passed": passed, "demo": True, "gate_sha": "abcdef1234",
            "summary": f"DEMO GATE (looser thresholds)\n{'PASS' if passed else 'FAIL'}: dsr 0.4",
            "checks": {"dsr": {"passed": passed, "value": 0.4}}}


def card(slug, approach, **kw):
    return {"lens": "card", "slug": slug, "contribution": "method", "problem": "p", "motivation": "m",
            "approach": approach, "plan": "x", "features": ["dte"], **kw}


def msg(body, sender="coder", room=CTL):
    return {"type": "message", "room_id": room, "sequence": 1,
            "payload": {"addressed": True, "sender": sender + "-id", "sender_name": sender, "body": body}}


@pytest.fixture
def env(tmp_path):
    con = db.connect(tmp_path / "r.db")
    runner, desks = StubRunner(), []
    cfg = {"humans": [HUMAN], "control_room": CTL, "root": tmp_path, "build_cap": 2, "gate": "demo"}
    o = Orchestrator(con, FakeAgent(), cfg, runner=runner, desk=lambda hid, room: desks.append((hid, room)))
    return o, con, runner, desks, tmp_path


def actions(o, *msgs):
    out = []
    for m in msgs:
        out += o.handle(m)
    return out


def bodies(acts):
    return [kw.get("content") or kw.get("body") for op, kw in acts if op in ("post", "mention")]


def open_h1(o):
    """Two proposals -> judge -> h1 wins and gets a room."""
    a = actions(o, msg("@orchestrator propose " + json.dumps(card("expiry-mom", "momentum on expiry")), "proposer-card"),
                msg("@orchestrator propose " + json.dumps(card("orb-vol", "opening range breakout")), "proposer-lead"))
    a += o.handle(msg('@orchestrator h1 round 1: {"prefer": "A", "reason": "simpler"}', "judge"))
    return a


def write_spec(root, hid, **change):
    raw = yaml.safe_load((ROOT / "specs/examples/orb_vol.yaml").read_text())
    raw["id"] = hid
    for k, v in change.items():
        raw[k] = v
    (root / "specs").mkdir(exist_ok=True)
    (root / f"specs/{hid}.yaml").write_text(yaml.safe_dump(raw))


def freeze_verify(o, root, hid, room):
    write_spec(root, hid)
    return actions(o, msg(f"@orchestrator freeze {hid} specs/{hid}.yaml", "coder", room),
                   msg(f"verified {hid} round 1: spec matches card", "verifier", room))


def test_propose_judge_opens_room(env):
    o, con, *_ = env
    a = open_h1(o)
    judge = [b for b in bodies(a) if b.startswith("@judge")]
    assert len(judge) == 1 and "h1 round 1" in judge[0] and "h2" in judge[0]
    (create,) = [kw for op, kw in a if op == "create_room"]
    assert create["inputs"]["hid"] == "h1" and create["inputs"]["room"] == "h1-expiry-mom"
    assert con.execute("SELECT room_id FROM hypotheses WHERE id='h1'").fetchone()[0] == "room-1"
    assert con.execute("SELECT room_id FROM hypotheses WHERE id='h2'").fetchone()[0] is None   # waits


def test_judge_only_from_judge(env):
    o, *_ = env
    actions(o, msg("@orchestrator propose " + json.dumps(card("a-b", "x y")), "proposer-card"),
            msg("@orchestrator propose " + json.dumps(card("c-d", "z w")), "proposer-lead"))
    assert not [1 for op, _ in o.handle(msg('@orchestrator h1 round 1: {"prefer": "A"}', "proposer-card"))
                if op == "create_room"]


def test_propose_filters(env):
    o, con, *_ = env
    a = o.handle(msg("@orchestrator propose " + json.dumps(card("bad-feat", "x", features=["rsi"])), "proposer-card"))
    assert "rsi" in bodies(a)[0] and not con.execute("SELECT 1 FROM hypotheses").fetchone()
    db.add_hypothesis(con, "h7", "gap-fade", card("gap-fade", "fade large opening gaps"))
    db.set_status(con, "h7", "dead")
    a = o.handle(msg("@orchestrator propose " + json.dumps(card("gap-fade2", "fade large opening gaps")), "proposer-card"))
    assert "h7" in bodies(a)[0] and con.execute("SELECT count(*) FROM hypotheses").fetchone()[0] == 1


def test_freeze_then_verify_runs_trial(env):
    o, con, runner, _, root = env
    open_h1(o)
    write_spec(root, "h1")
    a = o.handle(msg("@orchestrator freeze h1 specs/h1.yaml", "coder", "room-1"))
    assert con.execute("SELECT status, spec_sha FROM hypotheses WHERE id='h1'").fetchone()[0] == "frozen"
    assert any(b.startswith("@verifier h1 round 1") for b in bodies(a))
    runner.verdicts = [verdict(False)]
    assert o.handle(msg("verified h1 round 1: ok", "coder", "room-1")) == []        # not the verifier
    a = o.handle(msg("verified h1 round 1: ok", "verifier", "room-1"))
    assert runner.trials == [("h1", "research", "demo")]
    assert any("DEMO GATE" in b and "h1 round 1" in b for b in bodies(a))


def test_freeze_rejects_mismatched_spec(env):
    o, con, _, _, root = env
    open_h1(o)
    write_spec(root, "h2")
    (root / "specs/h1.yaml").write_text((root / "specs/h2.yaml").read_text())
    a = o.handle(msg("@orchestrator freeze h1 specs/h1.yaml", "coder", "room-1"))
    assert "@coder h1 round 1" in bodies(a)[0]
    assert con.execute("SELECT status FROM hypotheses WHERE id='h1'").fetchone()[0] == "proposed"


def test_failed_gate_requests_patch(env):
    o, con, runner, _, root = env
    open_h1(o)
    runner.verdicts = [verdict(False)]
    a = freeze_verify(o, root, "h1", "room-1")
    (ask,) = [b for b in bodies(a) if b.startswith("@coder")]
    assert "patch allowed (1/4)" in ask and "specs/h1v2.yaml" in ask
    assert "2023-01" in ask and "2023-02" in ask        # research per-month P&L only
    assert con.execute("SELECT status FROM hypotheses WHERE id='h1'").fetchone()[0] == "patching"
    runner.verdicts = [verdict(False)]
    a = freeze_verify(o, root, "h1v2", "room-1")
    assert con.execute("SELECT room_id, kind FROM hypotheses WHERE id='h1v2'").fetchone() == ("room-1", "patch")
    assert any("patch allowed (2/4)" in b and "h1v3" in b for b in bodies(a))


def test_hard_stop_posts_dead_end_and_offers_fork(env):
    o, con, runner, _, root = env
    open_h1(o)
    runner.verdicts, runner.stop = [verdict(False)], "dsr_unreachable"
    a = freeze_verify(o, root, "h1", "room-1")
    assert con.execute("SELECT status FROM hypotheses WHERE id='h1'").fetchone()[0] == "dead"
    pm = [b for b in bodies(a) if b.startswith("@proposer-lead")]
    assert pm and "dsr_unreachable" in pm[0] and "fork h1" in pm[0]
    assert con.execute("SELECT count(*) FROM messages WHERE kind='postmortem' AND hyp_id='h1'").fetchone()[0] == 1
    a = o.handle(msg("@orchestrator fork h1 expiry-volgate", "proposer-lead"))
    (create,) = [kw for op, kw in a if op == "create_room"]
    assert create["inputs"]["hid"] == "h1.1" and create["inputs"]["room"] == "h1-1-expiry-volgate"
    assert ("link", {"child_room": "room-2", "parent_room": "room-1", "label": "forked from h1"}) in a


def test_only_humans_approve(env):
    o, con, runner, desks, root = env
    open_h1(o)
    runner.verdicts = [verdict(True), verdict(True)]
    a = freeze_verify(o, root, "h1", "room-1")
    assert runner.trials == [("h1", "research", "demo"), ("h1", "holdout", "demo")]
    assert any("approve h1" in b for b in bodies(a))
    assert o.handle(msg("@orchestrator approve h1", "coder", "room-1")) == []
    assert not con.execute("SELECT 1 FROM signoffs").fetchone()
    a = o.handle(msg("@orchestrator approve h1", HUMAN, "room-1"))
    assert con.execute('SELECT decision, "by" FROM signoffs').fetchone() == ("approve", HUMAN)
    (create,) = [kw for op, kw in a if op == "create_room"]
    assert create["inputs"]["room"] == "paper-h1"
    assert desks == [("h1", "room-2")]
    assert con.execute("SELECT status FROM hypotheses WHERE id='h1'").fetchone()[0] == "paper"


def test_reject_is_calibration(env):
    o, con, runner, _, root = env
    open_h1(o)
    runner.verdicts = [verdict(True), verdict(False)]
    freeze_verify(o, root, "h1", "room-1")
    o.handle(msg("@orchestrator reject h1 holdout too short", HUMAN, "room-1"))
    assert con.execute("SELECT status FROM hypotheses WHERE id='h1'").fetchone()[0] == "rejected"
    assert con.execute("SELECT note FROM signoffs").fetchone()[0] == "holdout too short"


def test_stop_halts_new_rooms(env):
    o, con, *_ = env
    assert o.handle(msg("@orchestrator stop", "proposer-card")) == []      # humans only
    o.handle(msg("@orchestrator stop", HUMAN))
    a = open_h1(o)
    assert not [1 for op, _ in a if op == "create_room"]
    a = o.handle(msg("@orchestrator resume", HUMAN))
    assert [kw["inputs"]["hid"] for op, kw in a if op == "create_room"] == ["h1"]


def test_ignores_own_messages(env):
    o, *_ = env
    assert o.handle(msg("@orchestrator status", "orchestrator")) == []
    m = msg("@orchestrator status")
    m["payload"]["sender"] = FakeAgent.agent_id
    assert o.handle(m) == []


def test_implement_creates_lit_room_and_links(env):
    o, con, *_ = env
    a = o.handle(msg("@orchestrator implement https://arxiv.org/abs/2611.03424", HUMAN))
    (create,) = [kw for op, kw in a if op == "create_room"]
    assert create["inputs"]["lit_id"] == "arxiv-2611-03424" and create["inputs"]["room"] == "lit-arxiv-2611-03424"
    assert ("link", {"child_room": "room-1", "parent_room": CTL, "label": "paper arxiv-2611-03424"}) in a
    assert not [1 for op, _ in o.handle(msg("@orchestrator implement arxiv:2611.03424", HUMAN)) if op == "create_room"]
    src = {"type": "arxiv", "id": "2611.03424", "url": "https://arxiv.org/abs/2611.03424", "title": "T"}
    a = actions(o, msg("@orchestrator propose " + json.dumps(card("lit-mom", "paper momentum", source=src)), "reader"),
                msg("@orchestrator propose " + json.dumps(card("other-one", "other idea")), "proposer-card"),
                msg('@orchestrator h1 round 1: {"prefer": "A", "reason": "r"}', "judge"))
    assert ("link", {"child_room": "room-2", "parent_room": "room-1", "label": "from paper"}) in a


def test_all_posts_contain_hid_and_round(env):
    o, con, runner, _, root = env
    a = o.handle(msg("@orchestrator implement ssrn:4567890", HUMAN))
    a += open_h1(o)
    runner.verdicts = [verdict(False)]
    a += freeze_verify(o, root, "h1", "room-1")
    runner.verdicts, runner.stop = [verdict(False)], "patch_budget"
    a += freeze_verify(o, root, "h1v2", "room-1")
    a += o.handle(msg("@orchestrator fork h1 expiry-volgate", "proposer-lead"))
    a += o.handle(msg("@orchestrator status", HUMAN))
    a += o.handle(msg("@orchestrator freeze h9 specs/h9.yaml", "coder"))         # unknown hid: error post
    runner.verdicts, runner.stop = [verdict(True), verdict(True)], None
    a += freeze_verify(o, root, "h1.1", "room-3")
    a += o.handle(msg("@orchestrator approve h1.1", HUMAN))
    bs = bodies(a)
    assert len(bs) > 10
    for b in bs:
        assert TAG.search(b), b
    assert len(set(bs)) == len(bs)        # no repeated text: Switch pauses on a duplicate kickoff


def test_refreeze_allowed_until_first_trial(env):
    o, con, runner, _, root = env
    open_h1(o)
    write_spec(root, "h1")
    o.handle(msg("@orchestrator freeze h1 specs/h1.yaml", "coder", "room-1"))
    write_spec(root, "h1", params={"thr": 0.004, "rvcap": 0.18}, grid={"thr": [0.003, 0.004, 0.005]})
    a = o.handle(msg("@orchestrator freeze h1 specs/h1.yaml", "coder", "room-1"))   # verifier sent it back
    assert any(b.startswith("@verifier h1 round 1") and "freeze #2" in b for b in bodies(a))
    runner.verdicts = [verdict(False)]
    o.handle(msg("verified h1 round 1: ok", "verifier", "room-1"))
    write_spec(root, "h1")
    a = o.handle(msg("@orchestrator freeze h1 specs/h1.yaml", "coder", "room-1"))   # after a trial: locked
    assert "freeze refused" in bodies(a)[0]
