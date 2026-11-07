"""Read-only research memory (design §5a/§8): FTS5 over cards and post-mortems, dead ends, features.

The `mem` FTS table is a TEMP index (never written to research.db), rebuilt from `hypotheses` and `messages` on every search.
"""
from __future__ import annotations

import json
import re

from qs import db, features as feats

DEAD = ("dead", "retired", "rejected", "holdout_fail")
SLOTS = ("contribution", "problem", "motivation", "approach", "plan")


def index(con) -> None:
    # ponytail: full rebuild per query, O(hypotheses); switch to triggers if memory grows past ~10k cards
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.mem USING fts5(hid UNINDEXED, approach, body)")
    con.execute("DELETE FROM mem")
    for hid, card in con.execute("SELECT id, card FROM hypotheses"):
        c = json.loads(card or "{}")
        con.execute("INSERT INTO mem VALUES (?,?,?)",
                    (hid, str(c.get("approach", "")), " ".join(str(v) for v in c.values())))
    con.execute("INSERT INTO mem SELECT hyp_id, '', body FROM messages "
                "WHERE kind='postmortem' AND hyp_id IS NOT NULL")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _row(con, hid: str, board: dict | None = None) -> dict | None:
    lb = (board or {r["id"]: r for r in db.leaderboard(con)}).get(hid)
    if lb is None:
        return None
    card = json.loads(con.execute("SELECT card FROM hypotheses WHERE id=?", (hid,)).fetchone()[0] or "{}")
    checks = (lb["verdict"] or {}).get("checks", {})
    val = lambda k: checks.get(k, {}).get("value", lb["metrics"].get(k))  # noqa: E731
    paper = con.execute("SELECT ts, kind, payload FROM paper WHERE hyp_id=? ORDER BY rowid DESC LIMIT 1",
                        (hid,)).fetchone()
    return {"hid": hid, "status": lb["status"], "lineage": lb["lineage"], **{s: card.get(s) for s in SLOTS},
            "source": card.get("source"), "verdict": (lb["verdict"] or {}).get("summary"),
            "passed": lb["passed"], "demo": lb["demo"], "dsr": val("dsr"), "pbo": val("pbo"),
            "holdout_passed": lb["holdout_passed"], "signoff": lb["signoff"],
            "paper": {"ts": paper[0], "kind": paper[1], "payload": json.loads(paper[2] or "null")} if paper else None}


def search(con, text: str, k: int = 5, column: str | None = None) -> list[dict]:
    toks = _tokens(text)
    if not toks:
        return []
    index(con)
    q = " OR ".join(f'"{t}"' for t in toks)
    if column:
        q = f"{column} : ({q})"
    best: dict[str, float] = {}   # best (lowest) bm25 per hid; rows arrive best first
    for hid, s in con.execute("SELECT hid, bm25(mem) FROM mem WHERE mem MATCH ? ORDER BY bm25(mem)", (q,)):
        best.setdefault(hid, s)
    hits = list(best.items())[:k]
    board = {r["id"]: r for r in db.leaderboard(con)}
    return [{**_row(con, hid, board), "score": s} for hid, s in hits]


def show(con, hid: str) -> dict | None:
    row = _row(con, hid)
    if row is None:
        return None
    row["card"] = json.loads(con.execute("SELECT card FROM hypotheses WHERE id=?", (hid,)).fetchone()[0] or "{}")
    row["trials"] = [{"id": i, "window": w, "passed": bool(p), "demo": bool(d), "summary": json.loads(v or "{}").get("summary")}
                     for i, w, p, d, v in con.execute('SELECT id, "window", passed, demo, verdict FROM trials '
                                                      "WHERE hyp_id=? ORDER BY id", (hid,))]
    row["notes"] = [{"ts": t, "kind": k, "body": b} for t, k, b in con.execute(
        "SELECT ts, kind, body FROM messages WHERE hyp_id=? AND kind IN ('postmortem','decision','signoff') "
        "ORDER BY id", (hid,))]
    return row


def deadends(con, limit: int = 20) -> list[dict]:
    hids = [r[0] for r in con.execute(f"SELECT id FROM hypotheses WHERE status IN {DEAD!r} "
                                      "ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))]
    board = {r["id"]: r for r in db.leaderboard(con)}
    out = []
    for hid in hids:
        pm = con.execute("SELECT body FROM messages WHERE hyp_id=? AND kind='postmortem' ORDER BY id DESC LIMIT 1",
                         (hid,)).fetchone()
        out.append({**_row(con, hid, board), "postmortem": pm[0] if pm else None})
    return out


def features() -> list[str]:
    return sorted(feats.REGISTRY)


def downgrade(con, card: dict) -> float:
    """IdeaTaste nearest-neighbour downgrade: Jaccard of the approach slots when the nearest
    approach match is a dead end, else 0."""
    hits = search(con, str(card.get("approach", "")), k=1, column="approach")
    if not hits or hits[0]["status"] not in DEAD:
        return 0.0
    a, b = set(_tokens(card.get("approach"))), set(_tokens(hits[0]["approach"]))
    return len(a & b) / len(a | b) if a | b else 0.0


_ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv[:\s-]+|^)(\d{4})[.-](\d{4,5})(?:v\d+)?", re.I)
_SSRN = re.compile(r"(?:abstract_id=|abstract=|ssrn[:\s-]+)(\d{5,8})", re.I)


def paper_id(text: str) -> tuple[str, str] | None:
    """arXiv/SSRN url or id -> (lit_id, canonical url): arxiv-2611-03424, ssrn-4567890."""
    text = (text or "").strip()
    if m := _ARXIV.search(text):
        return f"arxiv-{m[1]}-{m[2]}", f"https://arxiv.org/abs/{m[1]}.{m[2]}"
    if m := _SSRN.search(text):
        return f"ssrn-{m[1]}", f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={m[1]}"
    return None
