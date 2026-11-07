import json
from pathlib import Path

import httpx
import yaml

from qs.switch.client import SwitchAgent, kickoff_text, parse_command, parse_sse, register

ROOT = Path(__file__).resolve().parents[1]


def test_op_sends_auth_headers():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"result": {"room_id": "r1"}})

    a = SwitchAgent("http://sw", "ag1", "key1", transport=httpx.MockTransport(handler))
    out = a.create_room("version: 2\n", {"hid": "h12"})
    assert out == {"room_id": "r1"}
    req = seen[0]
    assert req.url.path == "/agents/ag1/ops/create_room_from_yaml"
    assert req.headers["Authorization"] == "Bearer key1"
    assert req.headers["X-Switch-Connection-Id"] == a.connection_id
    assert json.loads(req.content) == {"yaml": "version: 2\n", "inputs": {"hid": "h12"}}

    a.link("child", "parent", "forked from h12")
    assert json.loads(seen[-1].content) == {
        "source_room_id": "child", "target_room_id": "parent", "label": "forked from h12"}

    a.mention("r9", "go", names=["coder"])
    assert [r.url.path.rsplit("/", 1)[1] for r in seen[-2:]] == ["connect_to_room", "send_targeted_message"]
    assert json.loads(seen[-1].content) == {
        "body": "go", "target_names": ["coder"], "target_roles": [], "thread_id": None}

    a.post("r9", "hello")
    assert seen[-1].url.path == "/agents/ag1/message"
    assert json.loads(seen[-1].content) == {"room_id": "r9", "content": "hello"}


def test_register_body():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"id": "ag1", "api_key": "k"})

    assert register("http://sw", "tok", "orchestrator", "d", transport=httpx.MockTransport(handler)) == ("ag1", "k")
    body = json.loads(seen[0].content)
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert body["integration_profile"]["connection_model"] == "always_on"


def test_parse_commands():
    p = parse_command
    assert p("@orchestrator freeze h12 specs/h12.yaml") == {"cmd": "freeze", "hid": "h12", "spec_path": "specs/h12.yaml"}
    assert p("@orchestrator: freeze h12.1v2 specs/h12.1v2.yaml") == {
        "cmd": "freeze", "hid": "h12.1v2", "spec_path": "specs/h12.1v2.yaml"}
    assert p('@orchestrator propose {"problem": "x", "kill": ["y"]}') == {
        "cmd": "propose", "card": {"problem": "x", "kill": ["y"]}}
    assert p("@orchestrator approve h3") == {"cmd": "approve", "hid": "h3"}
    assert p("@orchestrator reject h3 costs eat it") == {"cmd": "reject", "hid": "h3", "note": "costs eat it"}
    assert p("@orchestrator fork h12 expiry-mom-volgate") == {"cmd": "fork", "hid": "h12", "slug": "expiry-mom-volgate"}
    for c in ("status", "stop", "resume"):
        assert p(f"@orchestrator {c}") == {"cmd": c}
    # malformed
    for bad in (
        "freeze h12 specs/h12.yaml",                       # not addressed
        "@orchestrator freeze h12 specs/h13.yaml",         # path does not match hid
        "@orchestrator freeze h12 ../qs/gate.py",
        "@orchestrator freeze h12v1 specs/h12v1.yaml",     # patch k >= 2
        "@orchestrator propose {not json",
        "@orchestrator propose [1, 2]",
        "@orchestrator reject h3",                         # note required
        "@orchestrator fork h12 one-two-three-four",       # slug <= 3 words
        "@orchestrator status now",
        "@orchestrator dance",
    ):
        assert p(bad) is None, bad


def test_kickoff_unique():
    a, b = kickoff_text("h12", 1, "coder"), kickoff_text("h13", 1, "coder")
    assert a != b
    assert "h12" in a and "round 1" in a
    assert kickoff_text("h12", 1, "coder") != kickoff_text("h12", 2, "coder")


def test_templates_v2():
    for name in ("hypothesis-room.v2.yaml", "control-room.v2.yaml"):
        doc = yaml.safe_load((ROOT / "templates" / name).read_text())
        assert doc["version"] == 2
        (room,) = [r for r in doc["resources"].values() if r["type"] == "room"]
        assert "{hid}" in room["kickoff"] and "round {round}" in room["kickoff"]
        assert room["users"] == ["{human}"] and room["bridge"] == "{bridge}"
    hyp = yaml.safe_load((ROOT / "templates/hypothesis-room.v2.yaml").read_text())["resources"]["hyp"]
    assert {r["name"] for r in hyp["roles"] if r["exclusive"]} == {"coder", "verifier", "validator"}


SSE = (
    b": keepalive\n\n"
    b'event: connection_state\ndata: {"cursor": 7, "generation": 1, "heartbeat_interval_seconds": 2}\n\n'
    b": keepalive\n\n"
    b'id: 8\nevent: message\ndata: {"type": "message", "room_id": "r1", "sequence": 8,\n'
    b'data:  "payload": {"body": "@orchestrator status", "addressed": true}}\n\n'
)


def test_sse_parse():
    frames = list(parse_sse(SSE.decode().splitlines()))
    assert [e for e, _ in frames] == ["connection_state", "message"]
    assert frames[1][1]["payload"]["body"] == "@orchestrator status"

    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, content=SSE)

    a = SwitchAgent("http://sw", "ag1", "key1", transport=httpx.MockTransport(handler))
    ev = next(a.events())
    assert ev["room_id"] == "r1" and a.cursor == 8
    q = seen[0].url.params
    assert seen[0].headers["Accept"] == "text/event-stream"
    assert (q["connection_id"], q["scope"], q["filter"]) == (a.connection_id, "all", "all")


def test_parse_verified_judged_implement():
    p = parse_command
    assert p("verified h12 round 3: spec matches card") == {
        "cmd": "verified", "hid": "h12", "round": 3, "note": "spec matches card"}
    assert p("@orchestrator verified h12.1v2 round 1") == {
        "cmd": "verified", "hid": "h12.1v2", "round": 1, "note": ""}
    assert p('@orchestrator h12.1 round 2: {"prefer": "B", "reason": "simpler"}') == {
        "cmd": "judged", "hid": "h12.1", "round": 2, "prefer": "B", "reason": "simpler"}
    assert p("@orchestrator implement arxiv.org/abs/2611.03424") == {
        "cmd": "implement", "ref": "arxiv.org/abs/2611.03424"}
    assert p("@orchestrator attach ssrn-4567890") == {"cmd": "attach", "lit_id": "ssrn-4567890"}
    for bad in (
        "@coder h12 round 1: not verified yet",
        "verified h12: no round",
        '@orchestrator h12 round 2: {"prefer": "C", "reason": "x"}',
        '@orchestrator h12 round 2: {"prefer": "A"',
        "@orchestrator implement",
        "@orchestrator implement two words",
        "@orchestrator attach h12",
        "@orchestrator attach arxiv-2611",
    ):
        assert p(bad) is None, bad


def test_lit_template_v2():
    doc = yaml.safe_load((ROOT / "templates/lit-room.v2.yaml").read_text())
    assert doc["version"] == 2
    (room,) = [r for r in doc["resources"].values() if r["type"] == "room"]
    assert "kickoff" not in room          # the orchestrator kicks the reader off once the paper is parsed
    assert doc["params"]["bridge"] == {**doc["params"]["bridge"], "type": "bridge", "default": ["Slack", "$first"]}
    assert room["bridge"] == "{bridge}" and room["users"] == ["{human}"]
    assert "{reader}" in room["agents"]
    assert {r["name"] for r in room["roles"] if r["exclusive"]} == {"reader"}
