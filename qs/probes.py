"""Bias probes: look-ahead perturbation, determinism, and the AST lint for fallback signals."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd

from qs.engine import run_spec
from qs.spec import Spec

PERTURB_COLS = ("open", "high", "low", "close", "spot", "vix")


def lookahead_probe(spec: Spec, bars: pd.DataFrame, n_cuts: int = 10, seed: int = 0, **kw) -> bool:
    """Perturb every bar after a cut (x1.03 plus noise); positions at or before the cut must not move.
    `kw` goes to run_spec (params, costs, contracts)."""
    base = run_spec(spec, bars, **kw).position.to_numpy()
    n = len(bars)
    if n < 3:
        return True
    rng = np.random.default_rng(seed)
    cuts = rng.choice(np.arange(n // 10, n - 1), size=min(n_cuts, n - 1 - n // 10), replace=False)
    cols = [bars.columns.get_loc(c) for c in PERTURB_COLS if c in bars.columns]
    for cut in sorted(cuts):
        pert = bars.copy()
        block = pert.iloc[cut + 1:, cols].to_numpy(float)
        pert.iloc[cut + 1:, cols] = block * 1.03 * (1 + rng.normal(0, 0.002, block.shape))
        got = run_spec(spec, pert, **kw).position.to_numpy()
        if not np.array_equal(base[: cut + 1], got[: cut + 1]):
            return False
    return True


def determinism_check(spec: Spec, bars: pd.DataFrame, **kw) -> bool:
    a, b = run_spec(spec, bars.copy(), **kw), run_spec(spec, bars.copy(), **kw)
    return (a.bets.equals(b.bets) and a.position.equals(b.position)
            and a.daily.equals(b.daily) and a.cost_total == b.cost_total)


ALLOWED_IMPORTS = {"numpy", "pandas", "math"}
BANNED_NAMES = {"open", "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "getattr",
                "setattr", "delattr", "input", "breakpoint", "exit", "quit", "help", "memoryview",
                "__builtins__", "__loader__", "__spec__"}
IO_ATTRS = {"to_csv", "to_parquet", "to_pickle", "to_sql", "to_excel", "to_json", "to_hdf", "to_feather",
            "to_html", "to_clipboard", "to_stata", "to_orc", "to_xml", "to_latex", "to_markdown", "tofile",
            "save", "savez", "savez_compressed", "savetxt", "load", "loadtxt", "fromfile", "genfromtxt",
            "memmap", "DataSource", "ctypeslib", "testing", "f2py", "distutils", "show_config"}


def lint_signal(path) -> list[str]:
    """AST lint of a fallback signal module. Returns problems (empty list = clean)."""
    src = Path(path).read_text()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error: {e.msg} (line {e.lineno})"]
    out = []
    for n in ast.walk(tree):
        line = getattr(n, "lineno", "?")
        if isinstance(n, ast.Import):
            out += [f"line {line}: import {a.name} not allowed" for a in n.names
                    if a.name.split(".")[0] not in ALLOWED_IMPORTS]
        elif isinstance(n, ast.ImportFrom):
            if n.level or (n.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                out.append(f"line {line}: from {n.module} import not allowed")
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.append(f"line {line}: global/nonlocal not allowed")
        elif isinstance(n, ast.Name) and n.id in BANNED_NAMES:
            out.append(f"line {line}: {n.id} not allowed")
        elif isinstance(n, ast.Attribute):
            if n.attr.startswith("__"):
                out.append(f"line {line}: dunder attribute {n.attr} not allowed")
            elif n.attr.startswith("read_") or n.attr in IO_ATTRS:
                out.append(f"line {line}: I/O attribute {n.attr} not allowed")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("__"):
            out.append(f"line {line}: dunder string {n.value!r} not allowed")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "signal" for n in tree.body):
        out.append("no top-level def signal(bars_upto_t, state)")
    return out
