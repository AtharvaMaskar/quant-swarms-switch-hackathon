"""Orchestrator: the deterministic state machine behind the Switch rooms (design §5, §5a, §7.4).

Agents talk; this decides. Every room post carries `{hid} round {r}` (Review Focus 5).
State lives in research.db: hypotheses/status, plus `meta` keys round, stopped, judge_pending,
ranked, round:{hid}, opened:{r}, lit:{lit_id}.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from qs import db, ids, memory
from qs.spec import SpecError, load_spec, spec_sha
from qs.switch.client import SLUG, kickoff_text, parse_command

ROOT = Path(__file__).resolve().parents[1]
HYP_TEMPLATE = ROOT / "templates/hypothesis-room.v2.yaml"
LIT_TEMPLATE = ROOT / "templates/lit-room.v2.yaml"
PROPOSERS = ("proposer-card", "proposer-lead", "proposer-contrarian")
TERMINAL = ("dead", "rejected", "signed", "paper", "retired")
HUMAN_ONLY = {"approve", "reject", "stop", "resume"}
# one-off room per promoted strategy; alerts and P&L only, so no template file
PAPER_YAML = """version: 2
params:
  hid: {type: string}
  room: {type: string}
  orchestrator: {type: agent, default: orchestrator}
resources:
  paper:
    type: room
    name: "{room}"
    description: "Paper trading for {hid}: decay alerts and daily P&L."
    agents: ["{orchestrator}"]
    users: ["{$creator}"]
"""

Action = tuple[str, dict]
log = logging.getLogger("qs.orchestrator")


class Orchestrator:
    def __init__(self, con: sqlite3.Connection, agent, cfg: dict, runner=None,
                 desk: Callable[[str, str], Any] | None = None):
        if runner is None:
            from qs import runner   # wave-2 module; tests inject a stub
        self.con, self.agent, self.cfg, self.runner, self.desk = con, agent, cfg, runner, desk
        self.root = Path(cfg.get("root", ROOT))
        self.ctl = cfg["control_room"]
        self._acts: list[Action] = []

    # ---- state helpers ---------------------------------------------------------------------------
    def _meta(self, key, default=None):
        r = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def _set(self, key, value) -> None:
        self.con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    @property
    def round(self) -> int:
        return self._meta("round", 1)

    def _hround(self, hid) -> int:
        return self._meta(f"round:{ids._parse(hid)[0]}", self.round)

    def _hyp(self, hid) -> dict | None:
        r = self.con.execute("SELECT id, kind, slug, card, spec_path, room_id, status FROM hypotheses WHERE id=?",
                             (hid,)).fetchone()
        return dict(zip(("id", "kind", "slug", "card", "spec_path", "room_id", "status"), r)) if r else None

    def _room(self, hid) -> str | None:
        h = self._hyp(hid)
        return h and h["room_id"]

    def _do(self, op: str, **kw):
        self._acts.append((op, kw))
        return getattr(self.agent, op)(**kw)

    def _post(self, room, content):
        return self._do("post", room_id=room, content=content)

    def _ask(self, room, name, body):
        return self._do("mention", room_id=room, body=body, names=[name])

    # ---- entry points ----------------------------------------------------------------------------
    def handle(self, msg: dict) -> list[Action]:
        """Apply one Switch envelope; returns the Switch actions taken, in order."""
        self._acts = []
        p = msg.get("payload") or {}
        sender, name = p.get("sender"), p.get("sender_name") or ""
        if sender == getattr(self.agent, "agent_id", None) or name == self.cfg.get("name", "orchestrator"):
            return []                               # our own kickoffs and posts
        c = parse_command(p.get("body", ""))
        if c is None:
            return []
        if c["cmd"] in HUMAN_ONLY and name not in self.cfg.get("humans", []) and sender not in self.cfg.get("humans", []):
            return []
        room = msg.get("room_id") or self.ctl
        getattr(self, f"_on_{c['cmd']}")(c, room, name)
        return self._acts

    def run(self) -> None:
        for ev in self.agent.events():
            if ev.get("type", "message") != "message":
                continue
            try:
                self.handle(ev)
            except Exception as e:   # one bad message must not kill the loop
                log.exception("handle failed")
                db.log(self.con, "analysis", f"orchestrator error: {e!r}", room_id=ev.get("room_id"))

    # ---- propose -> judge -> rooms ---------------------------------------------------------------
    def _on_propose(self, c, room, name):
        card, r, nxt = c["card"], self.round, ids.next_root(self.con)
        slug, approach = card.get("slug"), card.get("approach")
        if not (isinstance(slug, str) and re.fullmatch(SLUG, slug) and isinstance(approach, str) and approach):
            return self._post(room, f"{nxt} round {r}: card dropped: needs a slug (<= 3 words) and an approach")
        bad = sorted(set(card.get("features", [])) - set(memory.features()))
        if bad:
            return self._post(room, f"{nxt} round {r}: card {slug} dropped: features not in the registry: {bad}")
        pen = memory.downgrade(self.con, card)
        if pen >= self.cfg.get("dead_dup", 0.8):
            near = memory.search(self.con, approach, k=1, column="approach")[0]["hid"]
            return self._post(room, f"{nxt} round {r}: card {slug} dropped: near-duplicate of dead end {near}")
        db.add_hypothesis(self.con, nxt, slug, card)
        self._set(f"round:{nxt}", r)
        db.log(self.con, "proposal", json.dumps(card), room_id=room, author=name, hyp_id=nxt)
        self._post(room, f"{nxt} round {r}: registered card {slug} from {name} (dead-end penalty {pen:.2f})")
        self._rank()

    def _pool(self) -> list[str]:
        busy = set(self._meta("ranked", [])) | set(self._meta("judge_pending") or [])
        return [h for (h,) in self.con.execute(
            "SELECT id FROM hypotheses WHERE kind='root' AND status='proposed' AND room_id IS NULL "
            "ORDER BY created_at, rowid") if h not in busy]

    def _rank(self):
        """Pairwise After42 tournament: the judge prefers one of two cards; the winner is ranked."""
        pool = self._pool()
        if self._meta("judge_pending") or len(pool) < 2:
            return
        a, b = pool[:2]
        self._set("judge_pending", [a, b])
        cards = {h: json.loads(self._hyp(h)["card"]) for h in (a, b)}
        near = [{k: x[k] for k in ("hid", "status", "approach", "dsr", "signoff")}
                for x in memory.search(self.con, f"{cards[a]['approach']} {cards[b]['approach']}", k=7)
                if x["hid"] not in (a, b)][:5]
        self._ask(self.ctl, "judge", f"{kickoff_text(a, self.round, 'judge')}\n"
                                     f"A = {a}: {json.dumps(cards[a])}\nB = {b}: {json.dumps(cards[b])}\n"
                                     f"nearest past cards: {json.dumps(near)}")

    def _on_judged(self, c, room, name):
        pend = self._meta("judge_pending")
        if name not in self.cfg.get("judges", ["judge"]) or not pend or c["hid"] != pend[0]:
            return
        win, lose = pend if c["prefer"] == "A" else pend[::-1]
        self._set("judge_pending", None)
        self._set("ranked", self._meta("ranked", []) + [win])
        db.log(self.con, "critique", f"judge prefers {win} over {lose}: {c['reason']}", room_id=room,
               author=name, hyp_id=win)
        self._open_ranked()
        self._rank()

    def _open_ranked(self):
        ranked, r = self._meta("ranked", []), self.round
        while ranked and not self._meta("stopped") and self._meta(f"opened:{r}", 0) < self.cfg.get("build_cap", 2):
            hid = ranked.pop(0)
            self._set("ranked", ranked)
            self._set(f"opened:{r}", self._meta(f"opened:{r}", 0) + 1)
            self._open(hid)

    def _open(self, hid):
        """Room from the template; its kickoff (@coder {hid} round {r}) is posted by Switch."""
        h, r = self._hyp(hid), self.round
        self._set(f"round:{hid}", r)
        card = json.loads(h["card"])
        res = self._do("create_room", template_yaml=HYP_TEMPLATE.read_text(),
                       inputs={"hid": hid, "room": ids.room_name(hid, h["slug"]), "slug": h["slug"], "round": r,
                               "spec_doc": json.dumps(card, indent=2)})
        rid = res["room_id"]
        self.con.execute("UPDATE hypotheses SET room_id=? WHERE id=?", (rid, hid))
        if h["kind"] == "fork":
            par = ids.parent(hid)
            self._do("link", child_room=rid, parent_room=self._room(par), label=f"forked from {par}")
        src = card.get("source") or {}
        if (pid := memory.paper_id(src.get("url") or f"{src.get('type')}:{src.get('id')}")) \
                and (lit := self._meta(f"lit:{pid[0]}")):
            self._do("link", child_room=rid, parent_room=lit, label="from paper")
        db.log(self.con, "decision", f"opened room {ids.room_name(hid, h['slug'])}", room_id=rid, hyp_id=hid)
        self._post(self.ctl, f"{hid} round {r}: room {ids.room_name(hid, h['slug'])} opened")

    def _on_fork(self, c, room, name):
        par, r = c["hid"], self.round
        h = self._hyp(par)
        if not h or h["status"] not in ("dead", "retired"):
            return self._post(room, f"{par} round {r}: fork refused: only dead ends or retired hypotheses fork")
        try:
            hid = ids.next_fork(self.con, par)
        except ValueError as e:
            return self._post(room, f"{par} round {r}: fork refused: {e}")
        db.add_hypothesis(self.con, hid, c["slug"], {**json.loads(h["card"]), "slug": c["slug"], "forked_from": par})
        db.log(self.con, "proposal", f"fork of {par} by {name}", room_id=room, author=name, hyp_id=hid)
        self._open(hid)

    # ---- freeze -> verify -> gate ----------------------------------------------------------------
    def _on_freeze(self, c, room, name):
        hid, path = c["hid"], c["spec_path"]
        h, base = self._hyp(hid), ids.parent(hid)
        if h is None and ids.kind(hid) == "patch" and (ph := self._hyp(base)) and ph["status"] == "patching" \
                and ids.next_patch(self.con, base) == hid:
            db.add_hypothesis(self.con, hid, ph["slug"], json.loads(ph["card"]))
            self.con.execute("UPDATE hypotheses SET room_id=? WHERE id=?", (ph["room_id"], hid))
            h = self._hyp(hid)
        r = self._hround(hid)
        if h is None or not h["room_id"]:
            return self._post(room, f"{hid} round {r}: freeze refused: unknown hypothesis or no room yet")
        hroom = h["room_id"]
        try:
            spec = load_spec(self.root / path)
            if spec.id != hid:
                raise SpecError(f"spec id {spec.id!r} is not {hid}")
            if ids.kind(hid) == "patch":
                self.runner.check_patch(load_spec(self.root / self._hyp(base)["spec_path"]), spec)
            sha = spec_sha(self.root / path)
            if self.con.execute("SELECT 1 FROM trials WHERE hyp_id=?", (hid,)).fetchone():
                raise ValueError(f"{hid} already has a trial; write the next patch id instead")
            # db.freeze stamps frozen_at, which locks the sha; before the first trial a verifier
            # send-back may still change the spec (design §5 step 6), so lift the stamp first
            self.con.execute("UPDATE hypotheses SET frozen_at=NULL WHERE id=?", (hid,))
            db.freeze(self.con, hid, path, sha)
        except (SpecError, ValueError, sqlite3.DatabaseError) as e:
            return self._ask(hroom, "coder", f"@coder {hid} round {r}: freeze refused: {e}")
        n = 1 + self.con.execute("SELECT count(*) FROM messages WHERE kind='build' AND hyp_id=?", (hid,)).fetchone()[0]
        db.log(self.con, "build", f"frozen {path} sha {sha}", room_id=hroom, author=name, hyp_id=hid)
        self._post(hroom, f"{hid} round {r}: spec frozen ({path}, sha {sha[:12]}, schema ok, freeze #{n})\n"
                          f"```yaml\n{(self.root / path).read_text()}```")
        self._ask(hroom, "verifier", kickoff_text(hid, r, "verifier") + (f" (freeze #{n})" if n > 1 else ""))

    def _on_verified(self, c, room, name):
        hid = c["hid"]
        h = self._hyp(hid)
        if name not in self.cfg.get("verifiers", ["verifier"]) or not h or h["status"] != "frozen":
            return
        r, hroom, gate = self._hround(hid), h["room_id"], self.cfg.get("gate", "real")
        db.log(self.con, "verify", c["note"], room_id=hroom, author=name, hyp_id=hid)
        db.set_status(self.con, hid, "gating")
        try:
            v = self.runner.run_trial(self.con, hid, window="research", gate=gate)
        except Exception as e:
            db.set_status(self.con, hid, "frozen")
            return self._ask(hroom, "coder", f"@coder {hid} round {r}: trial refused: {e}")
        ok = bool(v["passed"])
        db.log(self.con, "result", v.get("summary", ""), room_id=hroom, hyp_id=hid)
        self._post(hroom, f"{hid} round {r}: research trial {'PASS' if ok else 'FAIL'}\n{v.get('summary', '')}")
        self._ask(hroom, "validator", kickoff_text(hid, r, "validator"))
        if ok:
            return self._holdout(hid, r, hroom, gate)
        stop = self.runner.hard_stop(self.con, hid, self.cfg.get("gate_cfg", {}))
        nxt = ids.next_patch(self.con, hid)
        n = int(nxt.rsplit("v", 1)[1]) - 1
        if not stop and n > self.cfg.get("patch_budget", 4):
            stop = "patch_budget"
        if stop:
            return self._dead(hid, r, hroom, stop, v.get("summary", ""))
        db.set_status(self.con, hid, "patching")
        self._ask(hroom, "coder", f"@coder {hid} round {r}: patch allowed ({n}/{self.cfg.get('patch_budget', 4)}). "
                                  f"Change exactly one rule in specs/{nxt}.yaml, then freeze {nxt} specs/{nxt}.yaml.\n"
                                  f"Research-window P&L by month (INR):\n{self._monthly(hid)}")

    def _monthly(self, hid) -> str:
        """Per-month P&L of the latest research trial; never holdout (design §7.4)."""
        r = self.con.execute("SELECT daily FROM trials WHERE hyp_id=? AND \"window\"='research' ORDER BY id DESC LIMIT 1",
                             (hid,)).fetchone()
        s = db._from_parquet(r[0])
        m = s.groupby(pd.to_datetime(s.index).to_period("M")).sum()
        return "\n".join(f"{k} {v:,.0f}" for k, v in m.items())

    def _dead(self, hid, r, hroom, reason, summary):
        base = ids._parse(hid)[0]
        for (x,) in self.con.execute("SELECT id FROM hypotheses WHERE id=? OR id LIKE ?", (base, f"{base}v%")).fetchall():
            db.set_status(self.con, x, "dead")
        db.log(self.con, "postmortem", f"hard stop {reason}. {summary}", room_id=hroom, hyp_id=hid)
        self._post(hroom, f"{hid} round {r}: hard stop ({reason}); dead end. The record stays in memory.")
        offer = (f"To fork it: @orchestrator fork {base} <slug>." if ids.depth(base) < ids.MAX_FORK_DEPTH
                 else "Fork depth cap reached; no fork.")
        self._ask(self.ctl, "proposer-lead", f"@proposer-lead {hid} round {r}: post-mortem please. {hid} hit the "
                                             f"hard stop ({reason}). {offer}")
        self._next_round()

    def _holdout(self, hid, r, hroom, gate):
        db.set_status(self.con, hid, "gate_pass")
        if db.holdout_used(self.con, hid):
            return self._post(hroom, f"{hid} round {r}: holdout already used; no second touch")
        v = self.runner.run_trial(self.con, hid, window="holdout", gate=gate)
        ok = bool(v["passed"])
        db.set_status(self.con, hid, "holdout_pass" if ok else "holdout_fail")
        db.log(self.con, "result", f"holdout: {v.get('summary', '')}", room_id=hroom, hyp_id=hid)
        self._post(hroom, f"{hid} round {r}: holdout {'PASS' if ok else 'FAIL'} (one touch, recorded)\n"
                          f"{v.get('summary', '')}")
        humans = self.cfg.get("humans", [])
        body = (f"{' '.join('@' + x for x in humans)} {hid} round {r}: sign-off needed. Reply "
                f"`@orchestrator approve {hid}` or `@orchestrator reject {hid} <note>`.").strip()
        self._do("mention", room_id=hroom, body=body, names=humans)

    # ---- human decisions -------------------------------------------------------------------------
    def _signable(self, hid, room):
        h = self._hyp(hid)
        if h and h["status"] in ("holdout_pass", "holdout_fail"):
            return h
        self._post(room, f"{hid} round {self._hround(hid) if h else self.round}: nothing to sign off "
                         f"(status {h and h['status']})")

    def _on_approve(self, c, room, name):
        hid = c["hid"]
        if not (h := self._signable(hid, room)):
            return
        r = self._hround(hid)
        db.signoff(self.con, hid, "approve", name)
        db.log(self.con, "signoff", f"approved by {name}", room_id=room, author=name, hyp_id=hid)
        db.set_status(self.con, hid, "signed")
        pname = "paper-" + hid.lower().replace(".", "-")
        prid = self._do("create_room", template_yaml=PAPER_YAML, inputs={"hid": hid, "room": pname})["room_id"]
        self._do("link", child_room=prid, parent_room=h["room_id"], label=f"paper desk for {hid}")
        self._post(prid, f"{hid} round {r}: approved by {name}; paper desk {'starting' if self.desk else 'not wired'}")
        if self.desk:
            self.desk(hid, prid)
            db.set_status(self.con, hid, "paper")
        self._post(h["room_id"], f"{hid} round {r}: approved by {name}; paper room {pname}")
        self._next_round()

    def _on_reject(self, c, room, name):
        hid = c["hid"]
        if not self._signable(hid, room):
            return
        db.signoff(self.con, hid, "reject", name, c["note"])
        db.log(self.con, "signoff", f"rejected by {name}: {c['note']} (calibration)", room_id=room, author=name, hyp_id=hid)
        db.set_status(self.con, hid, "rejected")
        self._post(room, f"{hid} round {self._hround(hid)}: rejected by {name}; logged as calibration")
        self._next_round()

    def _next_round(self):
        """A round ends when every room it opened is terminal; then the proposers go again."""
        r = self.round
        opened = [h for (h,) in self.con.execute("SELECT id FROM hypotheses WHERE kind!='patch' AND room_id IS NOT NULL")
                  if self._meta(f"round:{h}") == r]
        last = lambda h: self.con.execute(  # noqa: E731  latest version in the patch lineage
            "SELECT status FROM hypotheses WHERE id=? OR id LIKE ? ORDER BY rowid DESC LIMIT 1", (h, f"{h}v%")).fetchone()[0]
        if not opened or self._meta("stopped") or any(last(h) not in TERMINAL for h in opened):
            return
        self._set("round", r + 1)
        nxt = ids.next_root(self.con)
        for p in PROPOSERS:
            self._ask(self.ctl, p, kickoff_text(nxt, r + 1, p))
        self._open_ranked()
        self._rank()

    # ---- control ---------------------------------------------------------------------------------
    def _on_stop(self, c, room, name):
        self._set("stopped", True)
        db.log(self.con, "decision", f"stop by {name}", room_id=room, author=name)
        self._post(room, f"{ids.next_root(self.con)} round {self.round}: stopped by {name}; no new rooms or rounds")

    def _on_resume(self, c, room, name):
        self._set("stopped", False)
        db.log(self.con, "decision", f"resume by {name}", room_id=room, author=name)
        self._post(room, f"{ids.next_root(self.con)} round {self.round}: resumed by {name}")
        self._open_ranked()
        self._rank()

    def _on_status(self, c, room, name):
        rows = self.con.execute("SELECT id, status FROM hypotheses ORDER BY rowid").fetchall()
        lines = ", ".join(f"{h} {s}" for h, s in rows) or "none yet"
        n = self.con.execute("SELECT count(*) FROM messages").fetchone()[0]   # keeps repeated status posts distinct
        self._post(room, f"{ids.next_root(self.con)} round {self.round}: status"
                         f"{' (stopped)' if self._meta('stopped') else ''} (log #{n}): {lines}")

    # ---- paper intake (§5a) ----------------------------------------------------------------------
    def _on_implement(self, c, room, name):
        r = self.round
        pid = memory.paper_id(c["ref"])
        if not pid:
            return self._post(room, f"{ids.next_root(self.con)} round {r}: implement needs an arXiv or SSRN url/id")
        lit_id, url = pid
        if lit := self._meta(f"lit:{lit_id}"):
            return self._post(room, f"{lit_id} round {r}: already open in lit-{lit_id} ({lit})")
        rid = self._do("create_room", template_yaml=LIT_TEMPLATE.read_text(),
                       inputs={"lit_id": lit_id, "room": f"lit-{lit_id}", "round": r, "url": url})["room_id"]
        self._set(f"lit:{lit_id}", rid)
        self._do("link", child_room=rid, parent_room=self.ctl, label=f"paper {lit_id}")
        db.log(self.con, "reference", json.dumps({"lit_id": lit_id, "url": url, "ref": c["ref"], "by": name}),
               room_id=rid, author=name)
        self._post(room, f"{lit_id} round {r}: lit room lit-{lit_id} opened for {name}")
