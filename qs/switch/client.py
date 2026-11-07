"""Switch agent-protocol client: HTTP ops up, SSE events down.

Wire facts checked against switch-core (docs/official/internals/agent-protocol.md,
bridges/agent/api/handlers.py, bridges/agent/operations/definitions.py).
"""
from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Iterable, Iterator

import httpx

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
HID = r"h\d+(?:\.\d+){0,3}(?:v(?:[2-9]|[1-9]\d+))?"   # h12, h12v2, h12.1, h12.1.2v3
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+){0,2}"                 # at most 3 words
LIT = r"arxiv-\d{4}-\d{4,5}|ssrn-\d+"                  # memory.paper_id lit ids

# Our process is reachable independent of any room: always_on. register-known only takes
# provider types (claude-code, codex...), so a custom agent registers via POST /agents.
ALWAYS_ON = {"connection_model": "always_on", "message_exchange": True,
             "pre_invocation_mediation": [], "post_invocation_mediation": [], "event_reporting": []}


def register(base_url: str, reg_token: str, name: str, description: str,
             transport: httpx.BaseTransport | None = None) -> tuple[str, str]:
    """Register once; returns (agent_id, api_key). The key is returned only this once."""
    if not NAME_RE.match(name):
        raise ValueError(f"bad agent name {name!r}")
    with httpx.Client(base_url=base_url.rstrip("/"), transport=transport, timeout=30) as c:
        r = c.post("/agents", headers={"Authorization": f"Bearer {reg_token}"},
                   json={"name": name, "description": description,
                         "connector_type": "custom", "integration_profile": ALWAYS_ON})
        r.raise_for_status()
        d = r.json()
        return d["id"], d["api_key"]


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, Any]]:
    """Yield (event, data) per SSE frame. Comments (keepalives) and `id:` lines are skipped."""
    event, data = "message", []
    for line in lines:
        if line == "":
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith(":"):
            continue
        else:
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "event":
                event = value
            elif field == "data":
                data.append(value)
    if data:
        yield event, json.loads("\n".join(data))


class SwitchAgent:
    def __init__(self, base_url: str, agent_id: str, api_key: str,
                 transport: httpx.BaseTransport | None = None):
        self.agent_id = agent_id
        self.connection_id = str(uuid.uuid4())   # kept for life: reconnects resume this connection
        self.cursor: int | None = None
        self.generation: int | None = None
        self._room: str | None = None            # room the connection is bound to for post/targeted ops
        self.http = httpx.Client(base_url=base_url.rstrip("/"), transport=transport, timeout=30,
                                 headers={"Authorization": f"Bearer {api_key}"})

    def op(self, name: str, **args) -> Any:
        r = self.http.post(f"/agents/{self.agent_id}/ops/{name}", json=args,
                           headers={"X-Switch-Connection-Id": self.connection_id})
        if r.is_error:   # surface Switch's reason (template validation codes etc.), not just the status
            raise httpx.HTTPStatusError(f"{r.status_code} on {name}: {r.text[:2000]}", request=r.request, response=r)
        return r.json()["result"]

    def thread(self, room_id: str, content: str) -> str:
        """Post a thread root in room_id; returns the virtual room id "<room_id>#<root event id>".
        Every post/mention to that id lands in the thread (one Slack channel, a thread per hypothesis/paper)."""
        if self._room != room_id:
            self.op("connect_to_room", room_id=room_id, include_general_instructions=False)
            self._room = room_id
        return f"{room_id}#{self.op('post_message', body=content)['event_id']}"

    def post(self, room_id: str, content: str, thread_id: str | None = None) -> dict:
        """Plain message; needs no connection. With thread_id (or a "<room>#<thread>" id), replies in that thread."""
        if "#" in room_id:
            room_id, thread_id = room_id.split("#", 1)
        if thread_id:
            if self._room != room_id:
                self.op("connect_to_room", room_id=room_id, include_general_instructions=False)
                self._room = room_id
            return self.op("post_message", body=content, thread_id=thread_id)
        r = self.http.post(f"/agents/{self.agent_id}/message", json={"room_id": room_id, "content": content})
        r.raise_for_status()
        return r.json()

    def mention(self, room_id: str, body: str, names=(), roles=(), thread_id: str | None = None) -> dict:
        """send_targeted_message acts on the connection's room, so claim it first."""
        if "#" in room_id:
            room_id, thread_id = room_id.split("#", 1)
        if self._room != room_id:
            self.op("connect_to_room", room_id=room_id, include_general_instructions=False)
            self._room = room_id
        return self.op("send_targeted_message", body=body, target_names=list(names),
                       target_roles=list(roles), thread_id=thread_id)

    def create_room(self, template_yaml: str, inputs: dict) -> dict:
        """Returns {room_id, room_name, ...}."""
        inputs = dict(inputs or {})
        if os.environ.get("QS_BRIDGE") and "{bridge}" in template_yaml:   # e.g. "Choice Slack"
            inputs.setdefault("bridge", os.environ["QS_BRIDGE"])
        return self.op("create_room_from_yaml", yaml=template_yaml, inputs=inputs)

    def link(self, child_room: str, parent_room: str, label: str) -> dict:
        if "#" in child_room or "#" in parent_room:     # threads carry lineage in their root text instead
            return {}
        return self.op("link_rooms", source_room_id=child_room, target_room_id=parent_room, label=label)

    def read_context(self, room_id: str, limit: int = 50) -> dict:
        room_id = room_id.split("#", 1)[0]
        """{threads: [{root, replies}], ...}; entries carry `attachments` [{filename, mimetype, mxc, ...}]."""
        return self.op("read_context", room_id=room_id, limit=limit)

    def download_media(self, room_id: str, mxc: str) -> bytes:
        """Attachment bytes from Switch's media store (attachments are pointers, never bytes)."""
        r = self.http.get(f"/agents/{self.agent_id}/rooms/{room_id}/media", params={"mxc": mxc})
        r.raise_for_status()
        return r.content

    def archive(self, room_id: str) -> dict:
        if "#" in room_id:                                # a thread is never archived; the channel stays
            return {}
        return self.op("archive_room", room_id=room_id)

    def _beat(self, interval: float, dead: threading.Event) -> None:
        while not dead.wait(interval):
            try:
                r = self.http.post(f"/agents/{self.agent_id}/connection/beat",
                                   json={"connection_id": self.connection_id,
                                         "generation": self.generation, "cursor": self.cursor})
            except httpx.HTTPError:
                continue
            if r.status_code in (404, 409):   # unknown / no stream attached: reopen, don't back off
                dead.set()

    def events(self) -> Iterator[dict]:
        """Yield `message` envelopes ({type, room_id, payload, sequence, ...}) forever.

        Reopens with the same connection id on drop, eviction or a refused beat;
        stops only when another stream takes the connection over.
        """
        url = f"/agents/{self.agent_id}/events"
        while True:
            dead = threading.Event()
            headers = {"Accept": "text/event-stream"}
            if self.cursor is not None:
                headers["Last-Event-ID"] = str(self.cursor)
            params = {"connection_id": self.connection_id, "scope": "all",
                      "filter": os.environ.get("QS_EVENT_FILTER", "all"), "start_from": "head"}
            try:
                with self.http.stream("GET", url, params=params, headers=headers,
                                      timeout=httpx.Timeout(30, read=60)) as r:
                    r.raise_for_status()
                    # keepalives arrive every 15 s, so a refused beat is noticed within one
                    lines = itertools.takewhile(lambda _: not dead.is_set(), r.iter_lines())
                    for event, data in parse_sse(lines):
                        if dead.is_set():
                            break
                        if event == "connection_state":
                            self.cursor, self.generation = data.get("cursor"), data.get("generation")
                            self._room = None
                            threading.Thread(target=self._beat, daemon=True,
                                             args=(data.get("heartbeat_interval_seconds", 2), dead)).start()
                        elif event == "evicted":
                            if data.get("code") == "taken_over":
                                return
                            break
                        elif event == "message":
                            self.cursor = data.get("sequence", self.cursor)
                            yield data
            except httpx.HTTPError:
                pass
            finally:
                dead.set()
            time.sleep(1)


_CMD = re.compile(r"(?:@orchestrator\b|^\s*deflate\b)[\s:,]*(\w+)\s*(.*)", re.S | re.I)
# the verifier posts `verified <hid> round <n>: ...` as a plain room message (agents/verifier.md)
_VERIFIED = re.compile(rf"^\s*(?:(?:@orchestrator|deflate)\b[\s:,]*)?verified\s+({HID})\s+round\s+(\d+)\b\s*:?\s*(.*)",
                       re.S | re.I)
# the judge replies `@orchestrator <hid> round <n>: {"prefer": "A"|"B", "reason": ...}` (agents/judge.md)
_JUDGED = re.compile(rf"(?:@orchestrator\b|^\s*deflate\b)[\s:,]*({HID})\s+round\s+(\d+)\s*:\s*(\{{.*\}})\s*$", re.S | re.I)


def parse_command(text: str) -> dict | None:
    """Parse a command addressed to @orchestrator; None when malformed.

    Sender checks (approve/reject are humans only) are the orchestrator's job.
    """
    text = text or ""
    if m := _VERIFIED.search(text):
        return {"cmd": "verified", "hid": m[1], "round": int(m[2]), "note": m[3].strip()}
    if m := _JUDGED.search(text):
        try:
            v = json.loads(m[3])
        except ValueError:
            return None
        if not isinstance(v, dict) or v.get("prefer") not in ("A", "B"):
            return None
        return {"cmd": "judged", "hid": m[1], "round": int(m[2]), "prefer": v["prefer"],
                "reason": str(v.get("reason", ""))}
    m = _CMD.search(text)
    if not m:
        return None
    cmd, rest = m.group(1).lower(), m.group(2).strip()
    if cmd in ("status", "stop", "resume"):
        return {"cmd": cmd} if not rest else None
    if cmd == "propose":
        try:
            card = json.loads(rest)
        except ValueError:
            return None
        return {"cmd": cmd, "card": card} if isinstance(card, dict) else None
    if cmd == "freeze" and (a := re.fullmatch(rf"({HID})\s+(\S+)", rest)):
        hid, path = a.groups()
        return {"cmd": cmd, "hid": hid, "spec_path": path} if path == f"specs/{hid}.yaml" else None
    if cmd == "approve" and re.fullmatch(HID, rest):
        return {"cmd": cmd, "hid": rest}
    if cmd == "reject" and (a := re.fullmatch(rf"({HID})\s+(.+)", rest, re.S)):
        return {"cmd": cmd, "hid": a.group(1), "note": a.group(2).strip()}
    if cmd == "fork" and (a := re.fullmatch(rf"({HID})\s+({SLUG})", rest)):
        return {"cmd": cmd, "hid": a.group(1), "slug": a.group(2)}
    if cmd == "implement" and re.fullmatch(r"\S+", rest):
        return {"cmd": cmd, "ref": rest}   # the orchestrator parses the arXiv/SSRN id
    if cmd == "attach" and re.fullmatch(LIT, rest):
        return {"cmd": cmd, "lit_id": rest}   # latest PDF attachment in that lit room
    return None


_ASK = {
    # no "@orchestrator" here: the kickoff speaks as the orchestrator, and must not address itself
    "coder": "write specs/{hid}.yaml from the card (no git), then post: deflate freeze {hid} specs/{hid}.yaml",
    "verifier": "spec specs/{hid}.yaml is frozen; check it matches the card and report in this room.",
    "validator": "the gate report for {hid} is in; explain it in plain language. Do not restate a verdict the report does not give.",
    "proposer-card": "propose cards (next id {hid}) from an untried literature/library card.",
    "proposer-lead": "propose cards (next id {hid}) from the recent post-mortems and learnings.",
    "proposer-contrarian": "propose cards (next id {hid}) from the losing periods of the reference rows.",
    "judge": "compare the two cards for {hid} and reply with the JSON verdict.",
}


def kickoff_text(hid: str, round: int, role: str) -> str:
    """Always carries the hid and `round {n}`: Switch pauses a run on a repeated kickoff."""
    ask = _ASK.get(role, "your turn; see the room brief.").format(hid=hid)
    return f"@{role} {hid} round {round}: {ask}"
