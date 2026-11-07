import json
import threading
import urllib.error
import urllib.request

import pandas as pd

from floor import server
from floor.switch_live import READ_ONLY, SwitchLive
from qs import db

CTL = "ctl-room"


class FakeAgent:
    """Stands in for qs.switch.client.SwitchAgent: records ops, serves canned read-only answers."""
    generation = 1

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def events(self):
        return iter(())

    def op(self, name, **kw):
        self.calls.append(name)
        if self.fail:
            raise ConnectionError("down")
        return {
            "get_room_detail": {"id": CTL, "name": "deflate-control", "bridge_display_name": "Choice Slack",
                                "agent_names": ["orchestrator", "judge"],
                                "agent_statuses": {"orchestrator": "live", "judge": "idle"},
                                "roles": [{"name": "judge", "exclusive": True, "held_by": ["judge"]}],
                                "connected_user_names": ["atharva"]},
            "read_context": {"threads": [{"root": {"id": "root1", "sender_name": "orchestrator", "body": "h1 thread",
                                                   "timestamp": 1794041602144},
                                          "replies": [{"id": "r1", "sender_name": "coder", "body": "spec frozen",
                                                       "timestamp": 1794041603144}]}]},
            "list_agents": [{"name": "judge", "display_name": "Judge", "connector_type": "Claude Code"}],
            "list_rooms": [{"room_id": CTL, "name": "deflate-control"}],
        }[name]


def _db(tmp_path):
    path = str(tmp_path / "r.db")
    con = db.connect(path)
    db.add_hypothesis(con, "h1", "expiry-mom", {"slug": "expiry-mom", "lens": "lead", "problem": "p", "kill": ["k"]})
    db.add_hypothesis(con, "h2", "vix-drift", {"slug": "vix-drift", "lens": "contrarian"})
    con.execute("UPDATE hypotheses SET room_id=? WHERE id='h1'", (f"{CTL}#root1",))
    con.execute("INSERT INTO meta VALUES ('control_room', ?)", (json.dumps(CTL),))
    con.execute("INSERT INTO meta VALUES ('judge_pending', ?)", (json.dumps(["h2", "h3"]),))
    daily = pd.Series([0.001, -0.002, 0.003, 0.0], index=pd.date_range("2023-01-02", periods=4, freq="B"))
    verdict = {"passed": False, "demo": True, "failed": ["dsr", "pbo"], "summary": "GATE FAIL  h1  2 of 3 checks failed",
               "paths": [[0.1] * 4], "n_eff": 1.0, "metrics": {"sr": 0.5, "gross_inr": 10.0, "cost_inr": 2.0, "net_inr": 8.0},
               "checks": {"dsr": {"passed": False, "value": 0.2, "threshold": ">0.95", "note": "n_eff 1.0, SR* 0.90"},
                          "pbo": {"passed": False, "value": 0.6, "threshold": "<=0.1", "note": ""},
                          "cpcv": {"passed": True, "value": {"median_sr": 0.3, "paths": [0.2, 0.3, 0.4]},
                                   "threshold": ">0.0, pos>=0.8", "note": "N=8 k=2"}}}
    db.record_trial(con, "h1", "research", {"thr": 1}, "s", "e", "g", True, daily, {"dsr": 0.2}, verdict, False)
    db.log(con, "result", "research trial FAIL", hyp_id="h1")
    con.close()
    return path


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read()


def test_floor_endpoints(tmp_path):
    path = _db(tmp_path)
    agent = FakeAgent()
    srv = server.serve(path, 0, [{"id": "buy-and-hold", "ref": True, "psr": float("nan")}], SwitchLive(agent=agent))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        _, body = _get(base, "/api/state")
        s = json.loads(body)
        assert not s["empty"] and s["kpis"]["trials"] == 1 and s["kpis"]["families"] == 2
        assert s["kpis"]["prior_trials"] == 987 and s["kpis"]["pass_rate"] == 0.0 and s["refs"][0]["psr"] is None
        cards = {c["hid"]: c for c in s["cards"]}
        assert cards["h1"]["gate"]["failed"] == ["dsr", "pbo"] and cards["h1"]["gate"]["demo"]
        assert cards["h1"]["column"] == "ranked" and cards["h1"]["sub"] == "speccing"
        assert (cards["h2"]["column"], cards["h2"]["sub"]) == ("ranked", "judging")
        assert s["switch"]["ok"] and s["switch"]["live"] == 1 and s["switch"]["room"]["bridge"] == "Choice Slack"
        slack = [m for m in s["feed"] if m["source"] == "switch"]
        assert {m["hyp_id"] for m in slack} == {"h1"} and any(m["source"] == "db" for m in s["feed"])

        _, body = _get(base, "/api/hyp/h1")
        d = json.loads(body)
        t = d["trials"][0]
        assert t["verdict"]["summary"].startswith("GATE FAIL") and "paths" not in t["verdict"]
        assert t["sr_star"] == 0.9 and len(t["daily"]["r"]) == 4 and d["hyp"]["card"]["problem"] == "p"
        assert d["thread"]["state"] == "ok" and d["thread"]["replies"][0]["body"] == "spec frozen"
        assert [m["kind"] for m in d["messages"]] == ["result"]

        _, body = _get(base, "/api/messages?hyp=h1")
        assert json.loads(body)[0]["body"] == "research trial FAIL"
        _, body = _get(base, "/api/switch")
        assert json.loads(body)["rooms"][0]["name"] == "deflate-control"
        for bad in ("/api/hyp/h9", "/api/hyp/..%2Fetc", "/api/paper/arxiv-0000-0000", "/nope"):
            try:
                _get(base, bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as e:
                assert e.code == 404
        status, html = _get(base, "/")
        assert status == 200 and b"Research Floor" in html and b"/api/stream" in html
        with urllib.request.urlopen(base + "/api/stream", timeout=10) as r:
            assert r.readline() == b"event: state\n" and json.loads(r.readline()[6:])["kpis"]["trials"] == 1
        assert set(agent.calls) <= READ_ONLY
    finally:
        srv.shutdown()


def test_floor_trials_table(tmp_path):
    path = _db(tmp_path)
    search = str(tmp_path / "search.db")
    con = db.connect(search)
    db.add_hypothesis(con, "h1", "gap-and-go", {"slug": "gap-and-go"})
    db.add_hypothesis(con, "h1.1", "gap-fade", {"slug": "gap-fade"})
    daily = pd.Series([0.001, -0.001, 0.002], index=pd.date_range("2023-01-02", periods=3, freq="B"))
    for hid, sr in (("h1", 0.4), ("h1.1", 0.9)):
        db.record_trial(con, hid, "research", {}, "s", "e", "g", True, daily, {"dsr": 0.3},
                        {"failed": ["dsr"], "metrics": {"sr": sr, "net_inr": 5.0}}, False)
    con.close()
    srv = server.serve(path, 0, search_db=search)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        run = json.loads(_get(base, "/api/trials?source=run")[1])
        assert run["available"] and run["n"] == 1 and run["passed"] == 0 and run["emax_sr"] is None
        r = run["rows"][0]
        assert (r["hid"], r["slug"], r["lens"], r["window"], r["demo"], r["failed"], r["dsr"], r["sr"], r["net_inr"]) == \
               ("h1", "expiry-mom", "lead", "research", True, ["dsr", "pbo"], 0.2, 0.5, 8.0)
        old = json.loads(_get(base, "/api/trials?source=search")[1])
        assert old["source"] == "search" and [x["hid"] for x in old["rows"]] == ["h1.1", "h1"]
        assert old["rows"][0]["lineage"] == "h1.1 ← h1" and old["emax_sr"] > 0 and old["prior_trials"] == 987
        d = json.loads(_get(base, "/api/hyp/h1.1?source=search")[1])
        assert d["trials"][0]["hyp_id"] == "h1.1" and d["thread"] == {"state": "none"}
    finally:
        srv.shutdown()
    srv = server.serve(path, 0, search_db=str(tmp_path / "missing.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert json.loads(_get(f"http://127.0.0.1:{srv.server_address[1]}", "/api/trials?source=search")[1])["available"] is False
    finally:
        srv.shutdown()


def test_floor_switch_offline_and_empty_db(tmp_path):
    path = _db(tmp_path)
    con = server.ro(path)
    s = server.state(con, [], SwitchLive(agent=FakeAgent(fail=True)))
    assert s["switch"]["ok"] is False and "did not answer" in s["switch"]["error"]
    assert server.hypothesis(con, "h1", SwitchLive(agent=FakeAgent(fail=True)))["thread"]["state"] == "offline"
    con.close()

    empty = str(tmp_path / "empty.db")
    db.connect(empty).close()
    con = server.ro(empty)
    s = server.state(con)
    assert server.trials(con)["rows"] == []
    assert s["empty"] and s["cards"] == [] and s["kpis"]["emax_sr"] is None and s["switch"] == {"ok": None}
    con.close()
    assert SwitchLive({}).snapshot(CTL)["configured"] is False
