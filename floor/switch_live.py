"""Live, read-only view of Switch for the floor: who is in #deflate-control, and the thread conversations.

Opens its OWN connection (SwitchAgent makes a fresh connection_id), keeps an event stream open in a daemon
thread because ops answer 409 without one, and only ever calls read-only ops:
get_room_detail, read_context, list_rooms, list_agents. It never posts, archives, invites or creates.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("floor.switch")
READ_ONLY = frozenset({"get_room_detail", "read_context", "list_rooms", "list_agents"})
KEYS = ("SWITCH_URL", "SWITCH_AGENT_ID", "SWITCH_API_KEY")


def load_env(path) -> dict:
    """KEY=VALUE lines from a .env file; never logged or returned to the page."""
    out = {}
    try:
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def _iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(float(ms) / 1000).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _msg(e: dict, thread: str | None) -> dict:
    return {"id": e.get("id"), "ts": _iso(e.get("timestamp")), "author": e.get("sender_name") or e.get("sender"),
            "body": e.get("body") or "", "thread": thread,
            "attachments": [a.get("filename") for a in e.get("attachments") or [] if a.get("filename")]}


class SwitchLive:
    def __init__(self, env: dict | None = None, agent=None, ttl: float = 5.0, wait: float = 10.0):
        env = env or {}
        self.configured = agent is not None or all(env.get(k) for k in KEYS)
        self.agent, self.ttl, self.wait = agent, ttl, wait
        self._env = {k: env.get(k) for k in KEYS}
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._started = False
        self.last_event: float | None = None

    @classmethod
    def from_env_file(cls, path, **kw) -> "SwitchLive":
        return cls(load_env(path), **kw)

    # ---- connection ------------------------------------------------------------------------------
    def start(self) -> "SwitchLive":
        if self._started or not self.configured:
            return self
        if self.agent is None:
            from qs.switch.client import SwitchAgent
            self.agent = SwitchAgent(self._env["SWITCH_URL"], self._env["SWITCH_AGENT_ID"], self._env["SWITCH_API_KEY"])
        self._started = True
        threading.Thread(target=self._drain, daemon=True, name="floor-switch-events").start()
        return self

    def _drain(self):
        """Hold the stream open so ops are accepted. Events are only used to expire the cache; never handled."""
        while True:
            try:
                for _ in self.agent.events():
                    self.last_event = time.time()
                    self._cache.clear()
            except Exception as e:   # the client retries internally; this guards anything unexpected
                log.warning("switch stream: %s", type(e).__name__)
            time.sleep(2)

    def _op(self, name, **kw):
        assert name in READ_ONLY, f"{name} is not a read-only op"
        t0 = time.monotonic()
        while getattr(self.agent, "generation", 1) is None and time.monotonic() - t0 < self.wait:
            time.sleep(0.1)                       # first connection_state not in yet
        return self.agent.op(name, **kw)

    # ---- reads -----------------------------------------------------------------------------------
    def snapshot(self, control_room: str | None) -> dict:
        """Cached ~ttl s: {ok, error, room, agents, threads}. ok=False means Switch is unreachable."""
        if not self.configured:
            return {"ok": False, "configured": False, "error": "Switch not configured (.env has no SWITCH_URL / agent keys)"}
        if not control_room:
            return {"ok": False, "configured": True, "error": "no control room recorded in research.db yet"}
        with self._lock:
            hit = self._cache.get(control_room)
            if hit and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
            self.start()
            try:
                room = self._op("get_room_detail", room_id=control_room)
                ctx = self._op("read_context", room_id=control_room, limit=100) or {}
                agents = {a.get("name"): a for a in self._op("list_agents") or []}
                snap = {"ok": True, "configured": True, "fetched_at": datetime.now().isoformat(timespec="seconds"),
                        "room": _room(room), "agents": _agents(room, agents), "threads": _threads(ctx)}
            except Exception as e:
                code = getattr(getattr(e, "response", None), "status_code", None)
                snap = {"ok": False, "configured": True,
                        "error": f"{type(e).__name__}{f' {code}' if code else ''}: Switch did not answer"}
                log.warning("switch snapshot failed: %s", snap["error"])
            self._cache[control_room] = (time.monotonic(), snap)
            return snap

    def rooms(self) -> list[dict]:
        try:
            return [{k: r.get(k) for k in ("room_id", "name", "description", "archived")} for r in self._op("list_rooms")]
        except Exception:
            return []


def _room(r: dict) -> dict:
    return {"id": r.get("id"), "name": r.get("name"), "bridge": r.get("bridge_display_name"),
            "channel": r.get("external_channel_id"), "users": r.get("connected_user_names") or [],
            "archived": bool(r.get("archived"))}


def _agents(room: dict, directory: dict) -> list[dict]:
    held = {}
    for role in room.get("roles") or []:
        for who in role.get("held_by") or []:
            held.setdefault(who if isinstance(who, str) else who.get("name"), []).append(role.get("name"))
    roles = [{"name": r.get("name"), "exclusive": bool(r.get("exclusive")), "held_by": r.get("held_by") or [],
              "about": r.get("instructions_preview")} for r in room.get("roles") or []]
    st = room.get("agent_statuses") or {}
    out = []
    for name in room.get("agent_names") or list(st):
        a = directory.get(name) or {}
        out.append({"name": name, "status": st.get(name, "unknown"), "display": a.get("display_name") or name,
                    "connector": a.get("connector_type"), "about": a.get("description"), "roles": held.get(name, []),
                    "team": re.sub(r"-.*", "", name)})
    return {"list": out, "roles": roles}


def _threads(ctx: dict) -> dict:
    """root id -> {root, replies} as plain messages."""
    out = {}
    for t in ctx.get("threads") or []:
        root = t.get("root") or {}
        rid = root.get("id")
        if not rid:
            continue
        out[rid] = {"root": _msg(root, rid), "replies": [_msg(e, rid) for e in t.get("replies") or []]}
    return out
