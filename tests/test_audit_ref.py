import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qs import db, ids
from qs.gate.gate import load_gate

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_ref", ROOT / "scripts/audit_ref.py")
audit_ref = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_ref)


def _series(mu, seed=0, n=500):
    idx = pd.bdate_range("2022-07-01", periods=n)
    return pd.Series(np.random.default_rng(seed).normal(mu, 0.005, n), index=idx)


def test_verdict_structure_and_not_run_never_pass(tmp_path):
    cfg, sha = load_gate(ROOT / "config/gate.yaml")
    for mu in (0.003, -0.001):   # strong edge and a loser
        v = audit_ref.audit(_series(mu), _series(0.0, seed=1), cfg, sha)
        run = [k for k, c in v["checks"].items() if c["run"]]
        assert sorted(run) == sorted(["psr", "dsr", "cpcv", "beats_refs", "concentration", "drawdown"])
        for k in audit_ref.NOT_RUN:
            assert v["checks"][k]["passed"] is False and v["checks"][k]["note"].startswith("not run")
            assert k not in v["failed"]
        assert v["passed"] == (not v["failed"]) == all(v["checks"][k]["passed"] for k in run)
        assert v["demo"] is False and v["gate_sha"] == sha
        lines = v["summary"].splitlines()
        assert lines[0] == "REFERENCE AUDIT · live 180-pt bot · stored P&L · partial gate (6 of 15 checks run)"
        assert "STT 0.02%" in v["summary"] and "2024-10-31" not in lines[0]
    assert audit_ref.audit(_series(-0.001), _series(0.0, 1), cfg, sha)["passed"] is False


def test_record_into_tmp_db(tmp_path):
    cfg, sha = load_gate(ROOT / "config/gate.yaml")
    con = db.connect(tmp_path / "r.db")
    db.add_hypothesis(con, "h1", "other", {})
    r = _series(-0.001)
    v = audit_ref.audit(r, _series(0.0, 1), cfg, sha)
    audit_ref.record(con, v, r)
    assert ids.next_root(con) == "h2"
    row = next(x for x in db.leaderboard(con) if x["id"] == "h0")
    assert row["kind"] == "root" and row["slug"] == "live-180pt-bot" and row["status"] == "dead"
    assert row["gate_sha"] == sha and row["demo"] is False and row["metrics"]["cpcv_median"] is not None
    assert con.execute("SELECT engine_sha FROM trials WHERE hyp_id='h0'").fetchone()[0] == "audit-stored-pnl"
    with pytest.raises(SystemExit):
        audit_ref.record(con, v, r)
    audit_ref.record(con, v, r, force_new_trial=True)
    assert con.execute("SELECT count(*) FROM trials WHERE hyp_id='h0'").fetchone()[0] == 2
