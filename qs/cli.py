"""`uv run python -m qs.cli <command>`. Memory commands are read-only and print JSON for agents."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from qs import db, ids, memory
from qs.orchestrator import ROOT

DEMO_CARDS = [
    {"lens": "card", "slug": "expiry-mom", "contribution": "method",
     "problem": "Does intraday momentum persist on index expiry days?",
     "motivation": "Expiry-day hedging flows on NSE index futures push in one direction late in the session.",
     "approach": "Follow the move since the open on expiry days when dte is zero.",
     "plan": "ret_since(session_open) > p.thr and dte == 0, exit at 15:15.",
     "mechanism": "Option writers delta-hedge into the close.", "rule": "ret_open > p.thr and dte <= 0",
     "params": {"thr": {"value": 0.003, "source": "median absolute open-to-noon move, sandbox window"}},
     "features": ["ret_since", "dte"], "datasets": ["nifty_fut_1m"], "kill": ["CPCV median path SR <= 0"]},
    {"lens": "lead", "slug": "orb-vol", "contribution": "filter",
     "problem": "Opening range breakouts fail in high-vol regimes.",
     "motivation": "Calm days trend after an early imbalance; volatile days mean-revert.",
     "approach": "Trade the opening-range breakout only when realized vol is below a cap.",
     "plan": "ret_since(session_open) beyond p.thr and realized_vol(20) < p.rvcap.",
     "mechanism": "Late liquidity follows early order-flow imbalance.", "rule": "abs(ret_open) > p.thr and rv < p.rvcap",
     "params": {"thr": {"value": 0.003, "source": "specs/examples/orb_vol.yaml"}},
     "features": ["ret_since", "realized_vol"], "datasets": ["nifty_fut_1m"], "kill": ["fails the cost ratio"]},
    {"lens": "contrarian", "slug": "gap-fade", "contribution": "replication",
     "problem": "Large overnight gaps partly revert in the first hour.",
     "motivation": "Buy-and-hold lost most on gap-down opens that recovered intraday.",
     "approach": "Fade large opening gaps toward the previous close.",
     "plan": "gap_pct beyond p.gap, exit at 10:30.", "mechanism": "Overnight overreaction to global cues.",
     "rule": "gap_pct < -p.gap", "params": {"gap": {"value": 0.006, "source": "90th pct |gap|, sandbox window"}},
     "features": ["gap_pct"], "datasets": ["nifty_fut_1m"], "kill": ["exposure null percentile < 0.95"]},
]


def _out(x) -> None:
    json.dump(x, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _gate_path(demo: bool) -> str:
    return str(ROOT / ("config/gate.demo.yaml" if demo else "config/gate.yaml"))


def run_orchestrator(con, a) -> None:
    from qs.gate.gate import load_gate
    from qs.orchestrator import Orchestrator
    from qs.switch.client import SwitchAgent

    os.chdir(ROOT)   # spec paths in the DB (specs/<hid>.yaml) are repo-relative
    agent = SwitchAgent(os.environ["SWITCH_URL"], os.environ["SWITCH_AGENT_ID"], os.environ["SWITCH_API_KEY"])
    ctl = a.control_room or os.environ.get("QS_CONTROL_ROOM") or json.loads(
        (con.execute("SELECT value FROM meta WHERE key='control_room'").fetchone() or ["null"])[0])
    if not ctl:
        ctl = agent.create_room((ROOT / "templates/control-room.v2.yaml").read_text(),
                                {"hid": ids.next_root(con), "round": 1})["room_id"]
        con.execute("INSERT OR REPLACE INTO meta VALUES ('control_room', ?)", (json.dumps(ctl),))
    humans = [h for h in (a.humans or os.environ.get("QS_HUMANS", "")).split(",") if h]
    if not humans:
        sys.exit("no humans: pass --humans or set QS_HUMANS (approve/reject are humans only)")

    def desk(hid, room):
        """Paper desk in its own process (qs.paper.desk main owns the feed/ref/expiry wiring)."""
        path = con.execute("SELECT spec_path FROM hypotheses WHERE id=?", (hid,)).fetchone()[0]
        subprocess.Popen([sys.executable, "-m", "qs.paper.desk", "--hid", hid, "--spec", path, "--replay",
                          "--room", room, "--control-room", ctl, "--db", a.db], cwd=ROOT)

    gate_cfg, _ = load_gate(_gate_path(a.demo))
    cfg = {"humans": humans, "control_room": ctl, "build_cap": a.build_cap, "gate": "demo" if a.demo else "real",
           "gate_cfg": gate_cfg}
    print(f"orchestrator up: control {ctl}, humans {humans}, gate {cfg['gate']}", file=sys.stderr)
    Orchestrator(con, agent, cfg, desk=desk).run()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="qs.cli")
    ap.add_argument("--db", default=os.environ.get("QS_DB", str(ROOT / "research.db")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run-orchestrator")
    r.add_argument("--demo", action="store_true")
    r.add_argument("--humans", help="comma-separated Switch user names allowed to approve/reject/stop")
    r.add_argument("--control-room")
    r.add_argument("--build-cap", type=int, default=2)
    t = sub.add_parser("trial")
    t.add_argument("hid")
    t.add_argument("--demo", action="store_true")
    c = sub.add_parser("calibrate")
    c.add_argument("--demo", action="store_true")
    sub.add_parser("leaderboard")
    sub.add_parser("demo-seed")
    m = sub.add_parser("memory").add_subparsers(dest="mcmd", required=True)
    ms = m.add_parser("search")
    ms.add_argument("text")
    ms.add_argument("-k", type=int, default=5)
    m.add_parser("show").add_argument("hid")
    m.add_parser("deadends").add_argument("--limit", type=int, default=20)
    m.add_parser("features")
    sub.add_parser("paper").add_subparsers(dest="pcmd", required=True).add_parser("fetch").add_argument("ref")
    a = ap.parse_args(argv)
    a.db = os.path.abspath(a.db)

    if a.cmd == "paper":
        from qs import papers
        p = papers.fetch(a.ref, out_root=ROOT / "papers")
        return _out({"lit_id": p.lit_id, "title": p.title, "method": p.method, "n_equations": len(p.equations),
                     "paths": {"md": p.md_path, "equations": p.equations_path, "pdf": p.pdf_path}, "notes": p.notes})
    if a.cmd == "memory" and a.mcmd == "features":
        return _out(memory.features())
    con = db.connect(a.db)
    if a.cmd == "memory":
        return _out({"search": lambda: memory.search(con, a.text, a.k), "show": lambda: memory.show(con, a.hid),
                     "deadends": lambda: memory.deadends(con, a.limit)}[a.mcmd]())
    if a.cmd == "run-orchestrator":
        return run_orchestrator(con, a)
    if a.cmd == "trial":
        from qs.runner import run_trial
        return _out(run_trial(con, a.hid, gate="demo" if a.demo else "real"))
    if a.cmd == "calibrate":
        from qs.data import load_bars, window
        from qs.gate.fixtures import calibrate
        from qs.gate.gate import load_gate
        cfg, sha = load_gate(_gate_path(a.demo))
        res = calibrate(load_bars(*window("research")), cfg, sha)
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"calibration:{sha}", json.dumps(res)))
        return _out({"gate_sha": sha, "reject_rate": res})
    if a.cmd == "leaderboard":
        return _out(db.leaderboard(con))
    if a.cmd == "demo-seed":
        added = []
        for i, card in enumerate(DEMO_CARDS, 1):
            hid = f"h{i}"
            if not con.execute("SELECT 1 FROM hypotheses WHERE id=?", (hid,)).fetchone():
                db.add_hypothesis(con, hid, card["slug"], card)
                db.log(con, "proposal", json.dumps(card), author="demo-seed", hyp_id=hid)
                added.append(hid)
        return _out({"added": added})


if __name__ == "__main__":
    main()
