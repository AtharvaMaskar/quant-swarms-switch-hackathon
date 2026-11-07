import json
import subprocess
import sys
from pathlib import Path

import pytest

from qs import db, memory

ROOT = Path(__file__).resolve().parents[1]

CARDS = {
    "h1": {"slug": "expiry-mom", "problem": "expiry day trend persistence",
           "approach": "trade momentum on expiry days when dte is zero", "plan": "ret_since open > thr and dte == 0"},
    "h2": {"slug": "orb-vol", "problem": "opening range breakouts",
           "approach": "breakout of the opening range gated by realized vol", "plan": "minute_of_day < 30"},
    "h3": {"slug": "gap-fade", "problem": "overnight gaps revert",
           "approach": "fade large opening gaps toward previous close", "plan": "gap_pct > thr"},
}


@pytest.fixture
def con(tmp_path):
    c = db.connect(tmp_path / "r.db")
    for hid, card in CARDS.items():
        db.add_hypothesis(c, hid, card["slug"], card)
    return c


def test_search_finds_similar(con):
    memory.index(con)
    hits = memory.search(con, "expiry momentum")
    assert hits[0]["hid"] == "h1"
    assert {"hid", "status", "approach", "dsr", "pbo", "signoff", "paper", "score"} <= set(hits[0])
    assert memory.search(con, "") == []


def test_search_reads_postmortems(con):
    db.set_status(con, "h3", "dead")
    db.log(con, "postmortem", "costs swamped the edge on illiquid strikes", hyp_id="h3")
    assert memory.search(con, "illiquid strikes")[0]["hid"] == "h3"
    assert memory.deadends(con)[0]["hid"] == "h3"


def test_downgrade_dead_approach(con):
    card = {"approach": "fade large opening gaps toward previous close", "problem": "gaps"}
    assert memory.downgrade(con, card) == 0.0          # h3 is alive
    db.set_status(con, "h3", "dead")
    assert memory.downgrade(con, card) == pytest.approx(1.0)
    assert 0 < memory.downgrade(con, {"approach": "fade opening gaps with a vol filter"}) < 1
    assert memory.downgrade(con, {"approach": "breakout of the opening range gated by realized vol"}) == 0.0


def test_parse_paper_ids():
    p = lambda s: memory.paper_id(s)[0]  # noqa: E731
    assert p("arxiv.org/abs/2611.03424") == "arxiv-2611-03424"
    assert p("https://arxiv.org/pdf/2611.03424v2") == "arxiv-2611-03424"
    assert p("arxiv:2611.03424") == "arxiv-2611-03424"
    assert p("papers.ssrn.com/sol3/papers.cfm?abstract_id=4567890") == "ssrn-4567890"
    assert p("ssrn:4567890") == "ssrn-4567890"
    assert memory.paper_id("arxiv-2611-03424")[1] == "https://arxiv.org/abs/2611.03424"
    assert memory.paper_id("https://example.com/paper") is None


def test_features_whitelist():
    assert {"ret_since", "dte", "vix", "basis"} <= set(memory.features())


def test_cli_memory_json(con, tmp_path):
    def run(*args):
        out = subprocess.run([sys.executable, "-m", "qs.cli", "--db", str(tmp_path / "r.db"), "memory", *args],
                             cwd=ROOT, capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    assert run("search", "expiry momentum")[0]["hid"] == "h1"
    assert run("show", "h2")["hid"] == "h2"
    assert run("deadends") == []
    assert "dte" in run("features")
