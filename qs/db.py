"""SQLite research DB. messages/trials are append-only; specs lock once frozen; holdout once."""
import io
import json
import sqlite3
from datetime import datetime

import pandas as pd

from qs import ids

STATUSES = ("proposed", "frozen", "gating", "patching", "dead", "gate_pass", "holdout_pass",
            "holdout_fail", "signed", "rejected", "paper", "retired")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS hypotheses(
  id TEXT PRIMARY KEY, family TEXT NOT NULL, parent TEXT,
  kind TEXT NOT NULL CHECK(kind IN ('root','patch','fork')),
  slug TEXT, card JSON, spec_path TEXT, spec_sha TEXT, room_id TEXT,
  status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN {STATUSES!r}),
  created_at TEXT NOT NULL DEFAULT (datetime('now')), frozen_at TEXT);
CREATE TABLE IF NOT EXISTS trials(
  id INTEGER PRIMARY KEY, hyp_id TEXT NOT NULL REFERENCES hypotheses(id), family TEXT NOT NULL,
  "window" TEXT NOT NULL, params JSON, spec_sha TEXT, engine_sha TEXT, gate_sha TEXT,
  demo INT NOT NULL, daily BLOB, metrics JSON, verdict JSON, passed INT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS holdout_once ON trials(hyp_id) WHERE "window"='holdout';
CREATE INDEX IF NOT EXISTS trials_family ON trials(family, "window");
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT (datetime('now')), kind TEXT NOT NULL,
  room_id TEXT, author TEXT, hyp_id TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS signoffs(
  hyp_id TEXT NOT NULL, ts TEXT NOT NULL DEFAULT (datetime('now')), decision TEXT NOT NULL,
  "by" TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS paper(
  hyp_id TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL, payload JSON);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TRIGGER IF NOT EXISTS messages_no_update BEFORE UPDATE ON messages
  BEGIN SELECT RAISE(ABORT, 'messages are append-only'); END;
CREATE TRIGGER IF NOT EXISTS messages_no_delete BEFORE DELETE ON messages
  BEGIN SELECT RAISE(ABORT, 'messages are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_update BEFORE UPDATE ON trials
  BEGIN SELECT RAISE(ABORT, 'trials are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_delete BEFORE DELETE ON trials
  BEGIN SELECT RAISE(ABORT, 'trials are append-only'); END;
CREATE TRIGGER IF NOT EXISTS spec_locked BEFORE UPDATE OF spec_sha, spec_path ON hypotheses
  WHEN OLD.frozen_at IS NOT NULL
   AND (NEW.spec_sha IS NOT OLD.spec_sha OR NEW.spec_path IS NOT OLD.spec_path)
  BEGIN SELECT RAISE(ABORT, 'spec is frozen'); END;
CREATE TRIGGER IF NOT EXISTS trial_freezes AFTER INSERT ON trials
  BEGIN UPDATE hypotheses SET frozen_at = NEW.started_at, spec_sha = COALESCE(spec_sha, NEW.spec_sha)
        WHERE id = NEW.hyp_id AND frozen_at IS NULL; END;

INSERT OR IGNORE INTO meta VALUES ('prior_trials_reported', '987');
INSERT OR IGNORE INTO meta VALUES ('prior_trials_note', 'reported, not counted (728 + 259 from the prior project)');
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path="research.db") -> sqlite3.Connection:
    # autocommit: every write lands immediately (append-only log, no lost trials on crash)
    con = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def add_hypothesis(con, hid, slug, card: dict, spec_path=None) -> None:
    con.execute("INSERT INTO hypotheses(id, family, parent, kind, slug, card, spec_path) "
                "VALUES (?,?,?,?,?,?,?)",
                (hid, ids.family(hid), ids.parent(hid), ids.kind(hid), slug, json.dumps(card), spec_path))


def freeze(con, hid, spec_path, spec_sha) -> None:
    cur = con.execute("UPDATE hypotheses SET spec_path=?, spec_sha=?, frozen_at=COALESCE(frozen_at, ?), "
                      "status='frozen' WHERE id=?", (spec_path, spec_sha, _now(), hid))
    if not cur.rowcount:
        raise KeyError(hid)


def set_status(con, hid, status) -> None:
    if not con.execute("UPDATE hypotheses SET status=? WHERE id=?", (status, hid)).rowcount:
        raise KeyError(hid)


def _to_parquet(s: pd.Series) -> bytes:
    buf = io.BytesIO()
    s.rename("r").to_frame().to_parquet(buf)
    return buf.getvalue()


def _from_parquet(b: bytes) -> pd.Series:
    return pd.read_parquet(io.BytesIO(b))["r"]


def record_trial(con, hid, window, params, spec_sha, engine_sha, gate_sha, demo,
                 daily: pd.Series, metrics, verdict, passed) -> int:
    t = _now()
    cur = con.execute(
        'INSERT INTO trials(hyp_id, family, "window", params, spec_sha, engine_sha, gate_sha, demo, '
        "daily, metrics, verdict, passed, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (hid, ids.family(hid), window, json.dumps(params), spec_sha, engine_sha, gate_sha, int(demo),
         _to_parquet(daily), json.dumps(metrics, default=float), json.dumps(verdict, default=str),
         int(passed), t, t))
    return cur.lastrowid


def family_trials(con, family, window="research") -> pd.DataFrame:
    rows = con.execute('SELECT id, daily FROM trials WHERE family=? AND "window"=? ORDER BY id',
                       (family, window)).fetchall()
    return pd.concat({i: _from_parquet(b) for i, b in rows}, axis=1) if rows else pd.DataFrame()


def holdout_used(con, hid) -> bool:
    return con.execute("SELECT 1 FROM trials WHERE hyp_id=? AND \"window\"='holdout'", (hid,)).fetchone() is not None


def log(con, kind, body, room_id=None, author="orchestrator", hyp_id=None) -> int:
    return con.execute("INSERT INTO messages(ts, kind, room_id, author, hyp_id, body) VALUES (?,?,?,?,?,?)",
                       (_now(), kind, room_id, author, hyp_id, body)).lastrowid


def signoff(con, hid, decision, by, note="") -> None:
    con.execute('INSERT INTO signoffs(hyp_id, ts, decision, "by", note) VALUES (?,?,?,?,?)',
                (hid, _now(), decision, by, note))


def _lineage(hid: str) -> str:
    chain = [hid]
    while (p := ids.parent(chain[-1])):
        chain.append(p)
    return " → ".join(reversed(chain))


def leaderboard(con) -> list[dict]:
    """One row per hypothesis: best research trial (passed first, then DSR), holdout, last signoff."""
    out = []
    for hid, fam, kind, slug, status in con.execute(
            "SELECT id, family, kind, slug, status FROM hypotheses ORDER BY created_at, id").fetchall():
        best = con.execute(
            "SELECT id, metrics, verdict, passed, demo, gate_sha FROM trials "
            "WHERE hyp_id=? AND \"window\"='research' "
            "ORDER BY passed DESC, json_extract(metrics,'$.dsr') DESC, id DESC LIMIT 1", (hid,)).fetchone()
        ho = con.execute("SELECT passed FROM trials WHERE hyp_id=? AND \"window\"='holdout'", (hid,)).fetchone()
        so = con.execute("SELECT decision FROM signoffs WHERE hyp_id=? ORDER BY rowid DESC LIMIT 1",
                         (hid,)).fetchone()
        out.append({
            "id": hid, "family": fam, "kind": kind, "slug": slug, "status": status, "lineage": _lineage(hid),
            "trial_id": best[0] if best else None,
            "metrics": json.loads(best[1]) if best else {},
            "verdict": json.loads(best[2]) if best else None,
            "passed": bool(best[3]) if best else None,
            "demo": bool(best[4]) if best else None,
            "gate_sha": best[5] if best else None,
            "holdout_passed": bool(ho[0]) if ho else None,
            "signoff": so[0] if so else None,
        })
    return out
