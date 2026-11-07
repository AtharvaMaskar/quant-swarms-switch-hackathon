import sqlite3

import pandas as pd
import pytest

from qs import db


def _daily(seed=0):
    idx = pd.date_range("2023-01-02", periods=10, freq="B")
    return pd.Series(range(seed, seed + 10), index=idx, dtype=float) / 1000


def _trial(con, hid, window="research", seed=0, passed=False, dsr=0.5):
    return db.record_trial(con, hid, window, {"k": 1}, "sha", "eng", "gate", False,
                           _daily(seed), {"dsr": dsr}, {"ok": passed}, passed)


@pytest.fixture
def con(tmp_path):
    return db.connect(tmp_path / "r.db")


def test_spec_locked_after_trial(con):
    db.add_hypothesis(con, "h1", "a b", {"problem": "x"})
    con.execute("UPDATE hypotheses SET spec_sha='s0' WHERE id='h1'")  # not frozen yet: fine
    db.freeze(con, "h1", "specs/h1.yaml", "s1")
    _trial(con, "h1")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE hypotheses SET spec_sha='tampered' WHERE id='h1'")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE hypotheses SET spec_path='other.yaml' WHERE id='h1'")
    db.set_status(con, "h1", "gating")  # other columns still mutable
    with pytest.raises(sqlite3.IntegrityError):
        db.set_status(con, "h1", "bogus")


def test_trial_insert_freezes(con):
    db.add_hypothesis(con, "h1", "a", {})
    _trial(con, "h1")
    row = con.execute("SELECT frozen_at, spec_sha FROM hypotheses WHERE id='h1'").fetchone()
    assert row[0] is not None and row[1] == "sha"
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE hypotheses SET spec_sha='x' WHERE id='h1'")


def test_messages_append_only(con):
    mid = db.log(con, "proposal", "hello {h1} round 1", room_id="r", hyp_id="h1")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE messages SET body='edited' WHERE id=?", (mid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM messages WHERE id=?", (mid,))
    db.add_hypothesis(con, "h1", "a", {})
    tid = _trial(con, "h1")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE trials SET passed=1 WHERE id=?", (tid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM trials WHERE id=?", (tid,))


def test_family_trials_includes_forks(con):
    for h in ("h12", "h12v2", "h12.1", "h13"):
        db.add_hypothesis(con, h, "s", {})
    a = _trial(con, "h12v2", seed=1)
    b = _trial(con, "h12.1", seed=2)
    _trial(con, "h13", seed=3)
    _trial(con, "h12", window="holdout")
    R = db.family_trials(con, "h12")
    assert sorted(R.columns) == sorted([a, b])
    pd.testing.assert_series_equal(R[a], _daily(1), check_names=False, check_freq=False)
    assert db.family_trials(con, "h99").empty


def test_holdout_once(con):
    db.add_hypothesis(con, "h1", "a", {})
    assert not db.holdout_used(con, "h1")
    _trial(con, "h1")
    assert not db.holdout_used(con, "h1")
    _trial(con, "h1", window="holdout")
    assert db.holdout_used(con, "h1")
    with pytest.raises(sqlite3.IntegrityError):
        _trial(con, "h1", window="holdout")


def test_meta_signoff_leaderboard(con):
    assert con.execute("SELECT value FROM meta WHERE key='prior_trials_reported'").fetchone()[0] == "987"
    db.add_hypothesis(con, "h1", "a", {})
    db.add_hypothesis(con, "h1.1", "b", {})
    _trial(con, "h1", dsr=0.2)
    best = _trial(con, "h1", dsr=0.9, passed=True)
    _trial(con, "h1", window="holdout", passed=True)
    db.signoff(con, "h1", "approve", "human", "lgtm")
    rows = {r["id"]: r for r in db.leaderboard(con)}
    assert rows["h1"]["trial_id"] == best and rows["h1"]["holdout_passed"] is True
    assert rows["h1"]["signoff"] == "approve" and rows["h1"]["demo"] is False
    assert rows["h1.1"]["trial_id"] is None and rows["h1.1"]["lineage"] == "h1 → h1.1"
