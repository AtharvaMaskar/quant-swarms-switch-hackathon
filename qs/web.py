"""Leaderboard page (design §12). `uv run python -m qs.web [--db research.db] [--port 8765]`.

GET /                 the page (polls the API every 5 s)
GET /api/leaderboard  JSON list: reference rows first (pinned), then hypotheses by score
GET /api/stats        JSON: trial/family counts, prior-project trials, calibration reject rates
Read-only: every request opens its own connection and only SELECTs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from qs import db
from qs.orchestrator import ROOT

log = logging.getLogger("qs.web")
ANN = np.sqrt(252)


def reference_rows() -> list[dict]:
    """Buy-and-hold and the live bot on the research window. Empty if data/ isn't built."""
    try:
        from qs import data, refs
        from qs.gate import stats
        bars = data.load_bars(*data.window("research"))
        out = []
        for name, s in refs.all_refs(bars).items():
            r = s.dropna().to_numpy(float)
            out.append({"id": {"buy_hold": "buy-and-hold", "live_bot": "live bot"}[name], "ref": True,
                        "status": "reference", "lineage": f"reference · SR {stats.sharpe(r) * ANN:+.2f} ann.",
                        "psr": stats.psr(r), "days": len(r)})
        return out
    except Exception as e:  # missing data must not take the board down
        log.warning("reference rows omitted: %s", e)
        return []


def _score(row) -> float:
    """Design §12: DSR minus penalties. ponytail: 0.25 per breached penalty, tune if ranking misleads."""
    m, dsr = row["metrics"], row["metrics"].get("dsr")
    if dsr is None:
        return float("-inf")
    pen = sum([(m.get("drawdown") or 0) > 0.20, (m.get("min_bets") or 1e9) < 50])
    return dsr - 0.25 * pen


def rows(con, refs: list[dict] | None = None) -> list[dict]:
    out = []
    for r in db.leaderboard(con):
        v, m = r.pop("verdict") or {}, r["metrics"]
        ch = v.get("checks", {})
        real = con.execute("SELECT passed FROM trials WHERE hyp_id=? AND \"window\"='research' AND demo=0 "
                           "ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        nulls = [ch[k]["passed"] for k in ("exposure_null", "beats_refs") if k in ch]
        out.append({**r, "ref": False, "score": _score(r),
                    "dsr": m.get("dsr"), "psr": m.get("psr"), "pbo": m.get("pbo"), "p_failure": m.get("p_failure"),
                    "cpcv_median": m.get("cpcv_median"), "cpcv_pos": (v.get("metrics") or {}).get("cpcv_pos"),
                    "beats_null": all(nulls) if nulls else None,
                    "real_gate": None if real is None else bool(real[0]),
                    "trials_family": con.execute("SELECT count(*) FROM trials WHERE family=? AND \"window\"='research'",
                                                 (r["family"],)).fetchone()[0]})
    out.sort(key=lambda x: (x["trial_id"] is None, -x["score"]))
    return (refs or []) + out


def stats(con) -> dict:
    from qs.gate.gate import load_gate
    shas = {load_gate(ROOT / f"config/{f}")[1]: lbl for f, lbl in (("gate.yaml", "real"), ("gate.demo.yaml", "demo"))}
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    calib = []
    for k, val in meta.items():
        if k.startswith("calibration:"):
            sha, rates = k.split(":", 1)[1], json.loads(val)
            calib.append({"gate": shas.get(sha, "old"), "sha": sha[:8],
                          "reject_rate": rates.get("reject_rate", rates)})   # fixtures.py nests it, cli.py doesn't
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    return {"trials": q("SELECT count(*) FROM trials"), "families": q("SELECT count(DISTINCT family) FROM hypotheses"),
            "hypotheses": q("SELECT count(*) FROM hypotheses"),
            "prior_trials": meta.get("prior_trials_reported"), "prior_note": meta.get("prior_trials_note"),
            "calibration": calib}


def make_handler(db_path: str, refs: list[dict]):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body: bytes, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if path not in ("/api/leaderboard", "/api/stats"):
                return self._send(404, b'{"error": "not found"}', "application/json")
            con = db.connect(db_path)
            try:
                body = rows(con, refs) if path == "/api/leaderboard" else stats(con)
            finally:
                con.close()
            self._send(200, json.dumps(_clean(body), default=str).encode(), "application/json")

        def log_message(self, *a):
            pass

    return H


def _clean(x):
    """NaN/inf -> None so browsers can parse the JSON."""
    if isinstance(x, float):
        return x if np.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


def serve(db_path: str, port: int = 8765, refs: list[dict] | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(db_path, refs or []))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="qs.web")
    ap.add_argument("--db", default=os.environ.get("QS_DB", str(ROOT / "research.db")))
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    srv = serve(os.path.abspath(a.db), a.port, reference_rows())
    print(f"leaderboard on http://127.0.0.1:{a.port}  (db {a.db})")
    srv.serve_forever()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deflate Leaderboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,100..900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root { --indigo:#1A1F6B; --indigo-2:#12164F; --lav:#C9CDFB; --dim:#9DA2E0; --red:#FF3B2F; --green:#0F9D58;
  --rule:rgba(201,205,251,.28); --dot:rgba(201,205,251,.16);
  --display:'Archivo','Arial Black',sans-serif; --mono:'JetBrains Mono',ui-monospace,Menlo,monospace; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; background-color:var(--indigo); color:#fff; font:400 13px/1.45 var(--mono);
  background-image:radial-gradient(var(--dot) 1.1px, transparent 1.5px); background-size:16px 16px;
  font-variant-numeric:tabular-nums; -webkit-font-smoothing:antialiased; }
main { max-width:1440px; margin:0 auto; padding:40px 16px 56px; }
.label { font:500 11px/1.2 var(--mono); text-transform:uppercase; letter-spacing:.08em; color:var(--dim); }
h1 { font:900 clamp(28px,8vw,104px)/.86 var(--display); font-stretch:125%; text-transform:uppercase;
  letter-spacing:-.035em; margin:14px 0 28px; }
h1 span { color:var(--lav); }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); border-top:1px solid var(--rule); border-left:1px solid var(--rule); margin-bottom:28px; }
.stat { min-width:0; overflow-wrap:anywhere; padding:14px 16px; border-right:1px solid var(--rule); border-bottom:1px solid var(--rule); }
.stat b { display:block; font:900 32px/1 var(--display); font-stretch:125%; margin-top:8px; }
.stat small { display:block; color:var(--dim); margin-top:6px; font-size:11px; }
.stat.prior b { color:var(--lav); }
.wrap { overflow-x:auto; border:1px solid var(--lav); }
table { width:100%; border-collapse:collapse; min-width:1180px; }
th { text-align:left; font:500 10.5px/1.2 var(--mono); text-transform:uppercase; letter-spacing:.07em; color:var(--lav);
  padding:12px 10px; border-bottom:1px solid var(--lav); white-space:nowrap; background:var(--indigo-2); }
td { padding:11px 10px; border-bottom:1px solid var(--rule); white-space:nowrap; vertical-align:middle; }
td.n { text-align:right; } th.n { text-align:right; }
tr.ref td { background:rgba(201,205,251,.10); color:var(--lav); }
tr.pinend td { border-bottom:1px solid var(--lav); }
td.id { font-weight:700; color:#fff; }
td.lin { color:var(--dim); white-space:normal; min-width:180px; }
.mute { color:rgba(201,205,251,.4); }
.chip { display:inline-block; font:600 10px/1 var(--mono); letter-spacing:.05em; text-transform:uppercase;
  padding:4px 6px; border:1px solid var(--lav); color:var(--lav); }
.chip.ok { background:var(--green); border-color:var(--green); color:#fff; }
.chip.live { background:var(--lav); color:var(--indigo); }
.chip.bad { background:var(--red); border-color:var(--red); color:#fff; }
.chip.ref { border-style:dashed; }
.chip.demo { border-color:#FFD23F; color:#FFD23F; }
.fail { color:var(--red); font-weight:700; } .pass { color:#7CE3A8; font-weight:700; }
.foot { display:flex; flex-wrap:wrap; gap:8px 24px; justify-content:space-between; margin-top:14px; }
.err { color:var(--red); }
</style></head>
<body><main>
<div class="label">Deflate · research floor · design §12</div>
<h1>Leader<span>board</span></h1>
<section class="stats" id="stats" aria-live="polite"></section>
<div class="wrap"><table>
<thead><tr><th>id</th><th>lineage</th><th>status</th><th class="n">DSR</th><th class="n">PSR</th><th class="n">PBO</th>
<th class="n">CPCV med SR</th><th class="n">% pos paths</th><th class="n">P[fail]</th><th>beats null</th><th>holdout</th>
<th class="n">trials in family</th><th>gate</th></tr></thead>
<tbody id="rows"><tr><td colspan="13" class="mute">loading…</td></tr></tbody></table></div>
<div class="foot label"><span>Reference rows are pinned and never ranked. Ranked by DSR minus penalties (max DD &gt; 20%, &lt; 50 bets).</span>
<span id="upd"></span></div>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num = (v, d=2) => v == null || typeof v !== 'number' ? '<span class="mute">—</span>' : v.toFixed(d);
const pct = v => v == null ? '<span class="mute">—</span>' : Math.round(v * 100) + '%';
const yn = v => v == null ? '<span class="mute">—</span>' : v ? '<span class="pass">YES</span>' : '<span class="fail">NO</span>';
const CHIP = {proposed:'', frozen:'', gating:'', patching:'', gate_pass:'ok', holdout_pass:'ok', holdout_fail:'bad',
  signed:'ok', paper:'live', dead:'bad', rejected:'bad', retired:'bad', reference:'ref'};
function gate(r) {
  if (r.ref || r.passed == null) return '<span class="mute">—</span>';
  const v = r.passed ? '<span class="pass">PASS</span>' : '<span class="fail">FAIL</span>';
  if (!r.demo) return v + ' <span class="mute">real</span>';
  const real = r.real_gate == null ? 'not run' : r.real_gate ? 'PASS' : 'FAIL';
  return `<span class="chip demo">DEMO GATE</span> ${v}<br><span class="mute">real gate: ${real}</span>`;
}
function row(r, i, all) {
  const pinEnd = r.ref && !(all[i + 1] || {}).ref;
  const ho = r.holdout_passed == null ? '<span class="mute">—</span>' : r.holdout_passed
    ? '<span class="pass">PASS</span> <span class="mute">seen*</span>' : '<span class="fail">FAIL</span> <span class="mute">seen*</span>';
  return `<tr class="${r.ref ? 'ref' : ''} ${pinEnd ? 'pinend' : ''}">
<td class="id">${esc(r.id)}</td><td class="lin">${esc(r.lineage)}${r.slug ? ' · ' + esc(r.slug) : ''}</td>
<td><span class="chip ${CHIP[r.status] ?? ''}">${esc(r.status.replace('_', ' '))}</span>${r.signoff ? ` <span class="mute">${esc(r.signoff)}</span>` : ''}</td>
<td class="n">${num(r.dsr, 3)}</td><td class="n">${num(r.psr, 3)}</td><td class="n">${num(r.pbo, 2)}</td>
<td class="n">${num(r.cpcv_median)}</td><td class="n">${pct(r.cpcv_pos)}</td><td class="n">${num(r.p_failure, 2)}</td>
<td>${yn(r.beats_null)}</td><td>${r.ref ? '<span class="mute">—</span>' : ho}</td>
<td class="n">${r.ref ? '<span class="mute">—</span>' : r.trials_family}</td><td>${gate(r)}</td></tr>`;
}
function stat(label, value, note, cls='') {
  return `<div class="stat ${cls}"><span class="label">${esc(label)}</span><b>${esc(value)}</b>${note ? `<small>${esc(note)}</small>` : ''}</div>`;
}
async function tick() {
  try {
    const [rows, s] = await Promise.all(['/api/leaderboard', '/api/stats'].map(u => fetch(u).then(r => r.json())));
    $('rows').innerHTML = rows.length ? rows.map(row).join('') : '<tr><td colspan="13" class="mute">no hypotheses yet</td></tr>';
    let h = stat('trials counted', s.trials, 'every run, failures and patches included')
      + stat('families', s.families, `${s.hypotheses} hypotheses`)
      + stat('prior project', s.prior_trials ?? '—', s.prior_note || 'reported, not counted', 'prior');
    for (const c of s.calibration) {
      const rr = Object.entries(c.reject_rate).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(' · ');
      const lo = Math.min(...Object.values(c.reject_rate));
      h += stat(`calibration · ${c.gate} gate ${c.sha}`, `${Math.round(lo * 100)}%`, 'min reject rate on no-skill fixtures: ' + rr);
    }
    $('stats').innerHTML = h;
    $('upd').textContent = '* holdout seen by a prior project · updated ' + new Date().toLocaleTimeString() + ' · refresh 5 s';
  } catch (e) { $('upd').innerHTML = '<span class="err">API unreachable: ' + esc(e.message) + '</span>'; }
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""

if __name__ == "__main__":
    main()
