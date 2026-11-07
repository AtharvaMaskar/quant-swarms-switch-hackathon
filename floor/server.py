"""Research floor: one live screen over research.db. `uv run python -m floor.server [--db research.db] [--port 8766]`.

GET /                    the page (floor/static/index.html)
GET /api/state           KPIs, board cards (one per patch line), lineage nodes, papers, refs, calibration, recent feed
GET /api/hyp/<hid>       drawer: card, frozen spec, every trial of the patch line (receipt, checks, daily), sign-off,
                         paper desk, messages
GET /api/trials          ?source=run|search  every trial, newest first, with the luck line (search = research.demo-search.db)
GET /api/messages        ?hyp=<hid>&limit=N  newest first
GET /api/paper/<lit_id>  a paper thread: paper.json summary, hypotheses it produced, the Slack thread from Switch
GET /api/switch          live Switch view of the control room: bridge, agents live/idle, roles, rooms
GET /api/stream          SSE: polls the DB (and the 5 s Switch cache) every 2 s, pushes `state` on any change
Read-only: DB connections use `mode=ro` and only SELECT; Switch is read through read-only ops only
(floor/switch_live.py). Nothing here writes anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import numpy as np
import pandas as pd

from qs import db, ids, memory
from qs.gate import stats as gstats

ROOT = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).with_name("static") / "index.html"
log = logging.getLogger("floor")

COLUMNS = ["proposed", "ranked", "frozen", "gating", "patching", "holdout", "signed", "paper", "closed"]
COLUMN_OF = {"frozen": "frozen", "gating": "gating", "patching": "patching", "gate_pass": "holdout",
             "holdout_pass": "holdout", "holdout_fail": "holdout", "signed": "signed", "paper": "paper",
             "dead": "closed", "rejected": "closed", "retired": "closed"}


# ---- helpers -----------------------------------------------------------------------------------
def ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{quote(os.path.abspath(path))}?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only=ON")
    return con


def _j(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return s


def _utc_local(ts):
    """hypotheses.created_at comes from SQLite datetime('now') (UTC); every other ts is local time."""
    if not ts:
        return ts
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None) \
            .isoformat(timespec="seconds")
    except ValueError:
        return ts


REF_ENGINE = "audit-stored-pnl"


def _is_ref(engine_sha, lens) -> bool:
    """A reference audit (e.g. h0, the live bot's stored P&L) is a pre-existing strategy, not a trial of this
    search: shown, never counted in trials, pass rate or the luck line."""
    return engine_sha == REF_ENGINE or lens == "reference"


def _base(hid):
    return ids._parse(hid)[0]


def _ver(hid):
    return ids._parse(hid)[1] or 1


def _meta(con) -> dict:
    return {k: _j(v, v) for k, v in con.execute("SELECT key, value FROM meta")}


def _fails(checks_json, failed=None) -> tuple[list[dict], int]:
    """Failed checks as [{k, v, thr}] (value against threshold) and the number of gating checks.
    The verdict's `failed` list is the authority; "(flag)" checks are advisory and not counted, as in the summary."""
    ch = _j(checks_json, {}) or {}
    failed = _j(failed, None)
    out = []
    for k, c in ch.items():
        if isinstance(c, dict) and (k in failed if failed is not None else not c.get("passed", True)):
            v = c.get("value")
            out.append({"k": k, "v": v.get("median_sr") if isinstance(v, dict) else v, "thr": c.get("threshold")})
    return out, sum("flag" not in str((c or {}).get("threshold", "")) for c in ch.values())


def _clean(x):
    """NaN/inf -> None so browsers can parse the JSON."""
    if isinstance(x, float):
        return x if np.isfinite(x) else None
    if isinstance(x, (np.floating, np.integer)):
        return _clean(x.item())
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _gates() -> dict:
    """gate_sha -> {label, cfg} for the committed gate files."""
    from qs.gate.gate import load_gate
    out = {}
    for f, lbl in (("gate.yaml", "real"), ("gate.demo.yaml", "demo")):
        try:
            cfg, sha = load_gate(ROOT / "config" / f)
            out[sha] = {"label": lbl, "cfg": cfg}
        except OSError:
            pass
    return out


def _budget() -> int:
    for g in _gates().values():
        if g["label"] == "real":
            return int((g["cfg"].get("thresholds") or {}).get("patch_budget", 4))
    return 4


def _calibration(con) -> list[dict]:
    try:
        from qs import web
        return web.stats(con)["calibration"]
    except Exception as e:  # gate files missing must not take the floor down
        log.warning("calibration omitted: %s", e)
        return []


def _paper_json(lit_id):
    p = ROOT / "papers" / lit_id / "paper.json"
    if not re.fullmatch(r"[a-z]+-[\w.-]+", lit_id) or not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _source_lit(card) -> str | None:
    src = (card or {}).get("source") or {}
    if not isinstance(src, dict):
        return None
    pid = memory.paper_id(src.get("url") or f"{src.get('type')}:{src.get('id')}")
    return pid[0] if pid else None


# ---- state -------------------------------------------------------------------------------------
EMPTY = {"empty": True, "kpis": {}, "columns": COLUMNS, "cards": [], "nodes": [], "papers": [], "feed": [],
         "refs": [], "calibration": []}


def _snap(sw, con) -> dict | None:
    """Switch snapshot for the control room, or None when the floor runs without Switch (tests, --no-switch)."""
    if sw is None:
        return None
    try:
        ctl = _meta(con).get("control_room")
    except sqlite3.OperationalError:
        ctl = None
    return sw.snapshot(ctl)


def _thread_owners(con) -> dict:
    """Slack thread root id -> ('hyp', hid) | ('paper', lit_id), from `<room>#<root>` ids in the DB."""
    out = {}
    for hid, rid in con.execute("SELECT id, room_id FROM hypotheses WHERE room_id LIKE '%#%' ORDER BY rowid"):
        out.setdefault(rid.split("#", 1)[1], ("hyp", hid))
    for k, v in _meta(con).items():
        if k.startswith("lit:") and isinstance(v, str) and "#" in v:
            out.setdefault(v.split("#", 1)[1], ("paper", k[4:]))
    return out


def _switch_public(snap) -> dict:
    if snap is None:
        return {"ok": None}
    pub = {k: v for k, v in snap.items() if k != "threads"}
    if snap.get("ok"):
        ag = (snap.get("agents") or {}).get("list", [])
        pub["live"] = sum(a["status"] == "live" for a in ag)
        pub["threads"] = len(snap.get("threads") or {})
    return pub


def state(con, refs=None, sw=None) -> dict:
    try:
        hyps = con.execute("SELECT rowid, id, family, parent, kind, slug, card, status, room_id, created_at, frozen_at "
                           "FROM hypotheses ORDER BY rowid").fetchall()
    except sqlite3.OperationalError:          # file exists but the schema was never created
        return {**EMPTY, "refs": refs or []}
    meta = _meta(con)
    ranked, pending = meta.get("ranked") or [], meta.get("judge_pending") or []
    trials = con.execute('SELECT t.id, t.hyp_id, t."window", t.demo, t.passed, t.started_at, json_extract(t.verdict, "$.failed"), '
                         'json_extract(t.verdict, "$.metrics.sr"), json_extract(t.metrics, "$.dsr"), '
                         'json_extract(t.verdict, "$.checks"), t.engine_sha, json_extract(h.card, "$.lens") '
                         "FROM trials t LEFT JOIN hypotheses h ON h.id = t.hyp_id ORDER BY t.id").fetchall()
    refs_n = sum(_is_ref(t[10], t[11]) for t in trials)
    trials = [t for t in trials if not _is_ref(t[10], t[11])]   # counted trials only from here on
    last_ts = dict(con.execute("SELECT hyp_id, max(ts) FROM messages WHERE hyp_id IS NOT NULL GROUP BY hyp_id"))
    signed = dict(con.execute('SELECT hyp_id, decision FROM signoffs ORDER BY rowid'))
    budget = _budget()

    by_hid = {}
    for t in trials:
        by_hid.setdefault(t[1], []).append(t)

    nodes, lines = [], {}
    for rowid, hid, fam, par, kind, slug, card, status, room, created, frozen in hyps:
        card = _j(card, {}) or {}
        nodes.append({"id": hid, "family": fam, "parent": par, "kind": kind, "slug": slug, "status": status})
        lines.setdefault(_base(hid), []).append((hid, slug, card, status, room, _utc_local(created), frozen))

    cards = []
    for base, vs in lines.items():
        vs.sort(key=lambda v: _ver(v[0]))
        hid, slug, card, status, room, created, frozen = vs[-1]
        root_card = vs[0][2]
        line_ids = [v[0] for v in vs]
        research = [t for h in line_ids for t in by_hid.get(h, []) if t[2] == "research"]
        holdout = [t for h in line_ids for t in by_hid.get(h, []) if t[2] == "holdout"]
        last = max(research, key=lambda t: t[0]) if research else None
        if status == "proposed" and len(vs) > 1:      # patch id registered, its spec not frozen yet
            col, sub = "patching", f"writing {hid}"
        elif status == "proposed":
            col = "ranked" if hid in ranked or hid in pending or room else "proposed"
            sub = ("judging" if hid in pending else f"ranked #{ranked.index(hid) + 1}" if hid in ranked
                   else "speccing" if room else "in pool")
        else:
            col, sub = COLUMN_OF.get(status, "proposed"), status.replace("_", " ")
        since = max([x for x in [created, frozen, *(last_ts.get(h) for h in line_ids)] if x] or [None])
        src = (root_card.get("source") or {}) if isinstance(root_card.get("source"), dict) else {}
        cards.append({
            "hid": hid, "base": base, "family": ids.family(hid), "kind": ids.kind(base), "slug": slug,
            "status": status, "column": col, "sub": sub,
            "lens": root_card.get("lens"), "contribution": root_card.get("contribution"),
            "source": src.get("url") or (f"{src.get('type')}:{src.get('id')}" if src.get("id") else None),
            "lit_id": _source_lit(root_card), "forked_from": root_card.get("forked_from"),
            "ref": root_card.get("lens") == "reference",
            "patches": len(vs) - 1, "budget": budget, "trials": len(research),
            "gate": None if last is None else {"trial": last[0], "passed": bool(last[4]), "demo": bool(last[3]),
                                               "failed": _j(last[6], []) or [], "sr": last[7], "dsr": last[8],
                                               "fails": _fails(last[9], last[6])[0], "n_checks": _fails(last[9], last[6])[1]},
            "holdout": None if not holdout else bool(holdout[-1][4]),
            "signoff": signed.get(hid), "since": since,
        })

    research = [t for t in trials if t[2] == "research"]
    srs = np.array([t[7] for t in research if t[7] is not None], float)
    emax = gstats.expected_max_sr(len(research), float(srs.var(ddof=1))) if len(srs) > 1 else None
    hb = con.execute("SELECT max(ts), max(id) FROM messages").fetchone()
    kpis = {
        "trials": len(trials), "research_trials": len(research), "reference_audits": refs_n,
        "families": len({n["family"] for n in nodes}), "hypotheses": len(nodes),
        "emax_sr": emax, "sr_sd": float(srs.std(ddof=1)) if len(srs) > 1 else None,
        "best_sr": float(srs.max()) if len(srs) else None,
        "passed": sum(t[4] for t in research), "pass_rate": (sum(t[4] for t in research) / len(research)) if research else None,
        "round": meta.get("round", 1), "stopped": bool(meta.get("stopped")),
        "heartbeat": hb[0], "last_msg": hb[1],
        "prior_trials": meta.get("prior_trials_reported"), "prior_note": meta.get("prior_trials_note"),
    }

    papers = []
    for k, rid in meta.items():
        if not k.startswith("lit:"):
            continue
        lit = k[4:]
        pj = _paper_json(lit) or {}
        papers.append({"lit_id": lit, "room": rid, "parsed": bool(meta.get(f"litpaper:{lit}")),
                       "title": pj.get("title"), "authors": pj.get("authors") or [], "url": pj.get("url"),
                       "published": pj.get("published"), "method": pj.get("method"),
                       "equations": len(pj.get("equations") or []) if pj else None,
                       "pdf_requests": meta.get(f"litask:{lit}", 0),
                       "hypotheses": [c["hid"] for c in cards if c["lit_id"] == lit]})

    snap = _snap(sw, con)
    feed = [{**m, "source": "db"} for m in messages(con, limit=200, trim=280)]
    if snap and snap.get("ok"):
        owners = _thread_owners(con)
        for root, t in snap["threads"].items():
            kind, who = owners.get(root, (None, None))
            for m in [t["root"], *t["replies"]]:
                feed.append({"id": m["id"], "ts": m["ts"], "kind": "slack", "author": m["author"], "source": "switch",
                             "hyp_id": who if kind == "hyp" else None, "lit_id": who if kind == "paper" else None,
                             "body": m["body"][:280] + ("…" if len(m["body"]) > 280 else "")})
            if kind == "paper":
                for p in papers:
                    if p["lit_id"] == who:
                        p["thread_msgs"] = 1 + len(t["replies"])
            elif kind == "hyp":
                for c in cards:
                    if c["base"] == _base(who):
                        c["thread_msgs"] = 1 + len(t["replies"])
        feed.sort(key=lambda m: m["ts"] or "", reverse=True)
        feed = feed[:300]
    return {"empty": not nodes and not trials, "kpis": kpis, "columns": COLUMNS, "cards": cards, "nodes": nodes,
            "papers": papers, "feed": feed, "refs": refs or [], "switch": _switch_public(snap),
            "calibration": _calibration(con), "gate_label": {s: g["label"] for s, g in _gates().items()}}


def messages(con, hyp=None, limit=200, trim=None) -> list[dict]:
    q, args = "SELECT id, ts, kind, author, hyp_id, room_id, body FROM messages", []
    if hyp:
        room = con.execute("SELECT room_id FROM hypotheses WHERE id=?", (hyp,)).fetchone()
        q += " WHERE hyp_id=?" + (" OR (hyp_id IS NULL AND room_id=?)" if room and room[0] else "")
        args = [hyp] + ([room[0]] if room and room[0] else [])
    rows = con.execute(q + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
    return [{"id": i, "ts": ts, "kind": k, "author": a, "hyp_id": h, "room": r,
             "body": (b or "")[:trim] + ("…" if trim and len(b or "") > trim else "") if trim else b}
            for i, ts, k, a, h, r, b in rows]


def _daily(blob) -> dict | None:
    if not blob:
        return None
    try:
        s = pd.read_parquet(io.BytesIO(blob))["r"]
    except Exception:
        return None
    idx = pd.to_datetime(s.index)
    return {"d": [d.strftime("%Y-%m-%d") for d in idx], "r": [round(float(x), 7) for x in s.to_numpy(float)]}


def _spec_text(hid, spec_path):
    for p in ([ROOT / spec_path] if spec_path else []) + [ROOT / "specs" / f"{hid}.yaml"]:
        p = p.resolve()
        if p.is_relative_to(ROOT / "specs") and p.is_file():
            return {"path": str(p.relative_to(ROOT)), "text": p.read_text()}
    return None


def _thread(snap, room_id) -> dict:
    """The Slack thread behind `<room>#<root>`, from Switch read_context."""
    if not room_id or "#" not in room_id:
        return {"state": "none"}
    if snap is None:
        return {"state": "no_switch"}
    if not snap.get("ok"):
        return {"state": "offline", "error": snap.get("error")}
    t = (snap.get("threads") or {}).get(room_id.split("#", 1)[1])
    return {"state": "ok", **t} if t else {"state": "not_in_context"}


def paper(con, lit_id, sw=None) -> dict | None:
    meta = _meta(con)
    rid = meta.get(f"lit:{lit_id}")
    if rid is None:
        return None
    pj = _paper_json(lit_id) or {}
    hyps = [h for h, card in con.execute("SELECT id, card FROM hypotheses ORDER BY rowid")
            if _source_lit(_j(card, {}) or {}) == lit_id]
    return {"lit_id": lit_id, "room": rid, "title": pj.get("title"), "authors": pj.get("authors") or [],
            "url": pj.get("url"), "published": pj.get("published"), "method": pj.get("method"),
            "abstract": pj.get("abstract"), "equations": len(pj.get("equations") or []) if pj else None,
            "hypotheses": hyps, "thread": _thread(_snap(sw, con), rid),
            "messages": [m for m in messages(con, limit=500) if m["room"] == rid][::-1]}


def hypothesis(con, hid, sw=None) -> dict | None:
    try:
        base = _base(hid)
    except ValueError:
        return None
    row = con.execute("SELECT id, family, parent, kind, slug, card, status, room_id, created_at, frozen_at, "
                      "spec_path, spec_sha FROM hypotheses WHERE id=?", (hid,)).fetchone()
    if not row:
        return None
    h = dict(zip(("id", "family", "parent", "kind", "slug", "card", "status", "room_id", "created_at", "frozen_at",
                  "spec_path", "spec_sha"), row))
    h["card"], h["created_at"] = _j(h["card"], {}) or {}, _utc_local(h["created_at"])
    line = [r[0] for r in con.execute("SELECT id FROM hypotheses WHERE id=? OR id LIKE ?", (base, f"{base}v%"))]
    line.sort(key=_ver)
    gates = _gates()
    trials = []
    for tid, th, win, demo, passed, started, gsha, params, metrics, verdict, blob in con.execute(
            f'SELECT id, hyp_id, "window", demo, passed, started_at, gate_sha, params, metrics, verdict, daily '
            f"FROM trials WHERE hyp_id IN ({','.join('?' * len(line))}) ORDER BY id", line):
        v = _j(verdict, {}) or {}
        v.pop("paths", None)                    # CPCV return matrix: large, and the path Sharpes are in checks.cpcv
        dsr_note = ((v.get("checks") or {}).get("dsr") or {}).get("note") or ""
        m = re.search(r"SR\* (-?[\d.]+)", dsr_note)
        g = gates.get(gsha) or {}
        cfg = g.get("cfg") or {}
        trials.append({"id": tid, "hyp_id": th, "window": win, "demo": bool(demo), "passed": bool(passed),
                       "started_at": started, "gate_sha": gsha, "gate": g.get("label", "old"),
                       "gate_cfg": {k: cfg.get(k) for k in ("cpcv_k", "embargo_frac", "pbo_S", "vol_target",
                                                            "exposure_null_n", "p_failure_target_sr")} if cfg else None,
                       "params": _j(params, {}), "metrics": _j(metrics, {}), "verdict": v,
                       "sr_star": float(m[1]) if m else None, "daily": _daily(blob)})
    family_n = con.execute("SELECT count(*) FROM trials WHERE family=? AND \"window\"='research'",
                           (h["family"],)).fetchone()[0]
    stop = con.execute("SELECT body FROM messages WHERE kind='postmortem' AND hyp_id IN "
                       f"({','.join('?' * len(line))}) ORDER BY id DESC LIMIT 1", line).fetchone()
    return {"hyp": h, "line": line, "spec": _spec_text(hid, h["spec_path"]), "trials": trials,
            "thread": _thread(_snap(sw, con), h["room_id"]),
            "family_trials": family_n, "budget": _budget(),
            "hard_stop": (re.match(r"hard stop (\S+)", stop[0]) or [None, None])[1] if stop else None,
            "children": [r[0] for r in con.execute("SELECT id FROM hypotheses WHERE parent=?", (hid,))],
            "signoffs": [dict(zip(("ts", "decision", "by", "note"), r)) for r in con.execute(
                'SELECT ts, decision, "by", note FROM signoffs WHERE hyp_id IN '
                f"({','.join('?' * len(line))}) ORDER BY rowid", line)],
            "paper": [{"hyp_id": a, "ts": b, "kind": c, "payload": _j(d)} for a, b, c, d in con.execute(
                f"SELECT hyp_id, ts, kind, payload FROM paper WHERE hyp_id IN ({','.join('?' * len(line))}) "
                "ORDER BY rowid", line)],
            "messages": sorted({m["id"]: m for x in line for m in messages(con, x, limit=500)}.values(), key=lambda m: m["id"])}


def trials(con) -> dict:
    """One row per trial (the "strategies tested" table) plus the luck-alone E[max SR] at the current count."""
    try:
        rows = con.execute(
            'SELECT t.id, t.hyp_id, h.slug, json_extract(h.card, "$.lens"), t."window", t.demo, t.passed, t.started_at, '
            'json_extract(t.verdict, "$.failed"), json_extract(t.metrics, "$.dsr"), json_extract(t.metrics, "$.psr"), '
            'json_extract(t.metrics, "$.pbo"), json_extract(t.metrics, "$.cpcv_median"), '
            'json_extract(t.verdict, "$.metrics.cpcv_pos"), json_extract(t.verdict, "$.metrics.net_inr"), '
            'json_extract(t.verdict, "$.metrics.n_bets"), json_extract(t.verdict, "$.checks.costs.value"), '
            'json_extract(t.verdict, "$.metrics.sr"), json_extract(t.verdict, "$.summary"), json_extract(t.verdict, "$.checks"), '
            't.engine_sha '
            "FROM trials t LEFT JOIN hypotheses h ON h.id = t.hyp_id ORDER BY t.id DESC").fetchall()
    except sqlite3.OperationalError:
        return {"rows": [], "n": 0, "passed": 0, "emax_sr": None}
    keys = ("id", "hid", "slug", "lens", "window", "demo", "passed", "when", "failed", "dsr", "psr", "pbo",
            "cpcv_median", "cpcv_pos", "net_inr", "bets", "cost_ratio", "sr", "summary", "checks", "engine")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["demo"], d["passed"], d["failed"] = bool(d["demo"]), bool(d["passed"]), _j(d["failed"], []) or []
        if d["window"] == "holdout" and d["summary"]:     # holdout verdicts carry net/PSR only in the summary
            m = re.search(r"net ([+-][\d.]+) of capital, PSR\(0\) ([\d.]+)", d["summary"])
            if m:
                d["net_frac"], d["psr"] = float(m[1]), float(m[2])
        d.pop("summary")
        d["fails"], d["n_checks"] = _fails(d.pop("checks"), d["failed"])
        d["ref"] = _is_ref(d.pop("engine"), d["lens"])
        try:
            d["lineage"] = " ← ".join(reversed(db._lineage(d["hid"]).split(" → ")))
        except ValueError:
            d["lineage"] = d["hid"]
        out.append(d)
    counted = [d for d in out if not d["ref"]]
    research = [d for d in counted if d["window"] == "research"]
    srs = np.array([d["sr"] for d in research if d["sr"] is not None], float)
    meta = _meta(con)
    return {"rows": out, "n": len(counted), "references": len(out) - len(counted), "research": len(research),
            "passed": sum(d["passed"] for d in research),
            "emax_sr": gstats.expected_max_sr(len(research), float(srs.var(ddof=1))) if len(srs) > 1 else None,
            "prior_trials": meta.get("prior_trials_reported"), "prior_note": meta.get("prior_trials_note")}


def fingerprint(con) -> str:
    try:
        r = con.execute("SELECT (SELECT max(id) FROM messages), (SELECT count(*) FROM trials), "
                        "(SELECT group_concat(id || ':' || status || ':' || ifnull(room_id, ''), ',') FROM hypotheses), "
                        "(SELECT group_concat(key || '=' || ifnull(value, ''), ',') FROM meta), "
                        "(SELECT count(*) FROM paper), (SELECT count(*) FROM signoffs)").fetchone()
    except sqlite3.OperationalError:
        return ""
    return hashlib.sha1(repr(r).encode()).hexdigest()


# ---- http --------------------------------------------------------------------------------------
def _sw_digest(sw, con) -> str:
    snap = _snap(sw, con)
    if snap is None:
        return ""
    if not snap.get("ok"):
        return "off:" + str(snap.get("error"))
    ag = [(a["name"], a["status"]) for a in (snap.get("agents") or {}).get("list", [])]
    th = sorted((k, len(t["replies"])) for k, t in (snap.get("threads") or {}).items())
    return repr((ag, th))


def make_handler(db_path: str, refs: list[dict], sw=None, search_path: str | None = None):
    search_path = search_path or str(Path(db_path).with_name("research.demo-search.db"))

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(_clean(obj), default=str).encode(), "application/json")

        def _con(self, source=None):
            path = search_path if source == "search" else db_path
            return ro(path) if os.path.exists(path) else None

        def do_GET(self):
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/":
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if u.path.startswith("/avatars/"):
                name = u.path[len("/avatars/"):].removesuffix(".png")
                f = ROOT / "assets" / "avatars" / f"{name}.png"
                if re.fullmatch(r"[a-z0-9-]+", name) and f.is_file():
                    return self._send(200, f.read_bytes(), "image/png")
                return self._send(404, b"", "image/png")
            if u.path == "/api/stream":
                return self._stream()
            src = "search" if q.get("source") == "search" else "run"
            con = self._con(src)
            sw_ = sw if src == "run" else None            # Switch threads belong to this run only
            try:
                if u.path == "/api/trials":
                    if not con:
                        return self._json({"source": src, "available": False, "rows": [], "n": 0})
                    return self._json({"source": src, "available": True, **trials(con)})
                if u.path == "/api/state":
                    return self._json(state(con, refs, sw) if con else {**EMPTY, "refs": refs, "no_db": db_path})
                if u.path == "/api/switch":
                    snap = _switch_public(_snap(sw, con) if con else None)
                    return self._json({**snap, "rooms": sw.rooms() if sw is not None and snap.get("ok") else []})
                if u.path == "/api/messages":
                    return self._json(messages(con, q.get("hyp"), min(int(q.get("limit", 200)), 2000)) if con else [])
                if u.path.startswith("/api/hyp/"):
                    d = hypothesis(con, u.path.rsplit("/", 1)[1], sw_) if con else None
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                if u.path.startswith("/api/paper/"):
                    d = paper(con, u.path.rsplit("/", 1)[1], sw) if con else None
                    return self._json(d) if d else self._json({"error": "not found"}, 404)
                self._json({"error": "not found"}, 404)
            except (sqlite3.OperationalError, ValueError) as e:
                self._json({"error": str(e)}, 400 if isinstance(e, ValueError) else 503)
            finally:
                if con:
                    con.close()

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last, beat = None, time.monotonic()
            try:
                while True:
                    con = self._con()
                    try:
                        fp = (fingerprint(con) + _sw_digest(sw, con)) if con else "nodb"
                        if fp != last:
                            body = state(con, refs, sw) if con else {**EMPTY, "refs": refs, "no_db": db_path}
                            self.wfile.write(b"event: state\ndata: " + json.dumps(_clean(body), default=str).encode()
                                             + b"\n\n")
                            self.wfile.flush()
                            last, beat = fp, time.monotonic()
                        elif time.monotonic() - beat > 15:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                            beat = time.monotonic()
                    except sqlite3.OperationalError as e:   # a writer mid-checkpoint: try again next tick
                        log.debug("stream poll: %s", e)
                    finally:
                        if con:
                            con.close()
                    time.sleep(2)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, *a):
            pass

    return H


def serve(db_path: str, port: int = 8766, refs: list[dict] | None = None, switch=None,
          search_db: str | None = None) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(os.path.abspath(db_path), refs or [], switch,
                                                                search_db and os.path.abspath(search_db)))
    srv.daemon_threads = True
    return srv


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="floor.server")
    ap.add_argument("--db", default=os.environ.get("QS_DB", str(ROOT / "research.db")))
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-refs", action="store_true", help="skip the buy-and-hold / live-bot reference rows")
    ap.add_argument("--env", default=str(ROOT / ".env"), help="where SWITCH_URL / SWITCH_AGENT_ID / SWITCH_API_KEY live")
    ap.add_argument("--no-switch", action="store_true", help="run from research.db alone")
    ap.add_argument("--search-db", default=None, help="earlier search shown by the Strategies toggle "
                                                     "(default: research.demo-search.db next to --db)")
    a = ap.parse_args(argv)
    refs = []
    if not a.no_refs:
        from qs import web
        refs = web.reference_rows()
    sw = None
    if not a.no_switch:
        from floor.switch_live import SwitchLive
        sw = SwitchLive.from_env_file(a.env).start()
    srv = serve(a.db, a.port, refs, sw, a.search_db)
    print(f"research floor on http://127.0.0.1:{a.port}  (db {a.db}, read-only; switch "
          f"{'off' if sw is None else 'configured' if sw.configured else 'not configured'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
