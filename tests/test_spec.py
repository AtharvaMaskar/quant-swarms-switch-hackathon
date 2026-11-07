import copy
from pathlib import Path

import pytest
import yaml

from qs.spec import SpecError, compile_expr, load_spec, spec_diff, spec_sha
from qs.probes import lint_signal

ROOT = Path(__file__).resolve().parents[1]
ORB = ROOT / "specs/examples/orb_vol.yaml"


def _write(tmp_path, data, name="s.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(data))
    return p


@pytest.mark.parametrize("src", [
    "__import__('os')",
    "p.__class__",
    "ret_open.__class__",
    "open('x')",
    "[x for x in ()]",
    "lambda: 1",
    "ret_open ** 2",
    "'a' < 'b'",
    "unknown_name > 1",
    "p.missing > 1",
    "max(ret_open, key=1)",
    "(lambda: 1)()",
    "ret_open if 1 else 2",
])
def test_expression_rejects_calls(src):
    with pytest.raises(SpecError):
        compile_expr(src, names={"ret_open", "rv"}, params={"thr"})


def test_expression_accepts_whitelist():
    compile_expr("not (ret_open > p.thr) and abs(rv) <= max(rv, 0.1) or -rv == min(1, 2) / 3 * 4 - 1",
                 names={"ret_open", "rv"}, params={"thr"})


def test_load_spec_ok():
    s = load_spec(ORB)
    assert s.id == "h0" and s.params["thr"] == 0.003 and s.grid == {"thr": [0.002, 0.003, 0.004]}


def test_load_spec_rejects_bad(tmp_path):
    base = yaml.safe_load(ORB.read_text())
    bad = []
    d = copy.deepcopy(base); d["features"]["x"] = {"fn": "no_such_fn"}; bad.append(d)
    d = copy.deepcopy(base); d["entry"]["long"] = "__import__('os')"; bad.append(d)
    d = copy.deepcopy(base); d["grid"] = {"thr": [0.001, 0.002, 0.003]}; bad.append(d)  # centre != frozen
    d = copy.deepcopy(base); d["grid"] = {"thr": [1, 2, 3, 4]}; bad.append(d)
    d = copy.deepcopy(base); d["features"]["rv"] = {"fn": "realized_vol", "bogus": 1}; bad.append(d)
    d = copy.deepcopy(base); del d["exit"]["stop_atr"]; bad.append(d)
    for i, d in enumerate(bad):
        with pytest.raises(SpecError):
            load_spec(_write(tmp_path, d, f"b{i}.yaml"))


def test_spec_sha_canonical(tmp_path):
    d = yaml.safe_load(ORB.read_text())
    a = _write(tmp_path, d, "a.yaml")
    b = tmp_path / "b.yaml"
    b.write_text(yaml.safe_dump(d, sort_keys=False, default_flow_style=True))
    assert spec_sha(a) == spec_sha(b) == spec_sha(ORB)
    d["params"]["thr"] = 0.0031
    assert spec_sha(_write(tmp_path, d, "c.yaml")) != spec_sha(a)


def test_spec_diff_one_rule(tmp_path):
    d = yaml.safe_load(ORB.read_text())
    d["exit"]["stop_atr"] = 2.0
    d["id"] = "h0v2"
    d["card"]["problem"] = "changed"
    assert spec_diff(load_spec(ORB), load_spec(_write(tmp_path, d))) == ["exit.stop_atr"]


def test_lint_signal(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import os\nimport numpy as np\ndef signal(b, s):\n    return eval('1')\n")
    issues = lint_signal(bad)
    assert any("os" in i for i in issues) and any("eval" in i for i in issues)
    good = tmp_path / "good.py"
    good.write_text("import numpy as np\nimport math\n\ndef signal(bars, state):\n"
                    "    return 1 if bars['close'].iloc[-1] > bars['open'].iloc[0] else 0\n")
    assert lint_signal(good) == []
    sneaky = tmp_path / "sneaky.py"
    sneaky.write_text("import pandas as pd\nfrom subprocess import run\n"
                      "def signal(b, s):\n    global X\n    pd.read_csv('x')\n"
                      "    return ().__class__.__bases__\n")
    issues = lint_signal(sneaky)
    assert len(issues) >= 4
