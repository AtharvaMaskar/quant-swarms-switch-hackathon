"""Freeze the running floor (:8766) into static files for GitHub Pages.
`python scripts/snapshot_floor.py OUT_DIR [--url http://127.0.0.1:8766]`"""
import argparse, json, pathlib, urllib.parse, urllib.request
from datetime import datetime

ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--url", default="http://127.0.0.1:8766")
a = ap.parse_args(); out = pathlib.Path(a.out); root = pathlib.Path(__file__).resolve().parents[1]

def get(path):
    with urllib.request.urlopen(a.url + path, timeout=60) as r:
        return r.read()

def save(rel, body):  # the bridge name and channel id identify the company Slack: blur them
    body = body.replace(b'"bridge": "Choice Slack"', b'"bridge": "Slack"').replace(b"Choice Slack", b"Slack").replace(b"C0CFPR901UN", b"")
    f = out / "api" / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(body)

state = get("/api/state"); save("state.json", state); S = json.loads(state)
for src in ("run", "search"):
    save(f"trials.{src}.json", get(f"/api/trials?source={src}"))
    hids = {r["hid"] for r in json.loads(get(f"/api/trials?source={src}")).get("rows", []) if r.get("hid")}
    if src == "run":
        hids |= {n["id"] for n in S.get("nodes", []) + S.get("refs", []) if n.get("id")}
    for h in sorted(hids):
        q = urllib.parse.quote(h)
        try: save(f"hyp/{q}.{src}.json", get(f"/api/hyp/{q}?source={src}"))
        except Exception as e: print("skip hyp", h, src, e)
for p in S.get("papers", []):
    lit = p.get("id") or p.get("lit_id") if isinstance(p, dict) else None
    if lit:
        q = urllib.parse.quote(lit, safe="")
        try: save(f"paper/{q}.json", get(f"/api/paper/{q}"))
        except Exception as e: print("skip paper", lit, e)

stamp = datetime.now().strftime("%d %b %Y %H:%M IST")
import shutil; shutil.copytree(root / "assets/avatars", out / "avatars", dirs_exist_ok=True)
page = (root / "floor/static/index.html").read_text()
page = page.replace("<head>", f"<head><script>window.FLOOR_SNAPSHOT={json.dumps(stamp)}</script>", 1)
(out / "index.html").write_text(page)
print("snapshot", stamp, "->", out, sum(1 for _ in (out / "api").rglob("*.json")), "files")
