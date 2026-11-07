import json
import threading
import urllib.request

import pandas as pd

from qs import db, web


def test_web_leaderboard_json(tmp_path):
    path = str(tmp_path / "r.db")
    con = db.connect(path)
    db.add_hypothesis(con, "h1", "expiry-mom", {"slug": "expiry-mom"})
    daily = pd.Series([0.001, -0.002, 0.003], index=pd.date_range("2023-01-02", periods=3, freq="B"))
    db.record_trial(con, "h1", "research", {}, "s", "e", "g", True, daily, {"dsr": 0.6, "psr": 0.7},
                    {"checks": {"exposure_null": {"passed": True}}, "metrics": {"cpcv_pos": 0.8}}, True)
    srv = web.serve(path, 0, [{"id": "buy-and-hold", "ref": True, "status": "reference", "psr": float("nan")}])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/api/leaderboard") as r:
            assert r.status == 200
            rows = json.loads(r.read())
        assert isinstance(rows, list) and rows[0]["ref"] and rows[0]["psr"] is None
        h1 = rows[1]
        assert (h1["id"], h1["dsr"], h1["cpcv_pos"], h1["beats_null"], h1["demo"], h1["real_gate"]) == \
               ("h1", 0.6, 0.8, True, True, None)
        assert "verdict" not in h1 and h1["trials_family"] == 1
        with urllib.request.urlopen(base + "/api/stats") as r:
            s = json.loads(r.read())
        assert s["trials"] == 1 and s["prior_trials"] == "987"
        with urllib.request.urlopen(base + "/") as r:
            assert b"Leader" in r.read()
    finally:
        srv.shutdown()
