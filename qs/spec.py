"""Declarative strategy spec: loading, validation, hashing, structured diff, safe expressions."""
from __future__ import annotations

import ast
import hashlib
import operator
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

import numpy as np
import yaml

from qs import features

SEGMENTS = {"futures", "options", "equity_intraday", "equity_delivery"}
MAX_EXPR_LEN = 1000


class SpecError(ValueError):
    pass


@dataclass
class Spec:
    id: str
    instrument: dict
    session: dict
    features: dict
    entry: dict
    exit: dict
    sizing: dict
    card: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    grid: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict, repr=False)
    path: str | None = None

    @property
    def names(self) -> set[str]:
        return set(self.features) | features.implicit_names()


def parse_hhmm(s, what="time") -> time:
    try:
        return datetime.strptime(str(s), "%H:%M").time()
    except ValueError:
        raise SpecError(f"{what}: expected 'HH:MM', got {s!r}") from None


# ---- restricted expressions ------------------------------------------------------------------
_CMP = {ast.Lt: operator.lt, ast.Gt: operator.gt, ast.LtE: operator.le, ast.GtE: operator.ge,
        ast.Eq: operator.eq}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CALLS = {"abs": lambda a: np.abs(a), "min": lambda *a: _fold(np.minimum, a), "max": lambda *a: _fold(np.maximum, a)}


def _fold(f, args):
    out = args[0]
    for a in args[1:]:
        out = f(out, a)
    return out


def compile_expr(src: str, names: set[str], params: set[str]) -> ast.Expression:
    """Parse and whitelist-check an expression. Raises SpecError on anything not allowed."""
    if not isinstance(src, str) or not src.strip() or len(src) > MAX_EXPR_LEN:
        raise SpecError(f"expression must be a non-empty string of <= {MAX_EXPR_LEN} chars")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise SpecError(f"bad expression {src!r}: {e.msg}") from None
    _check(tree.body, names, params, src)
    return tree


def _check(n, names, params, src):
    bad = lambda why: SpecError(f"{why} in {src!r}")  # noqa: E731
    if isinstance(n, ast.BoolOp) and isinstance(n.op, (ast.And, ast.Or)):
        for v in n.values:
            _check(v, names, params, src)
    elif isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.Not, ast.USub, ast.UAdd)):
        _check(n.operand, names, params, src)
    elif isinstance(n, ast.Compare):
        if not all(type(o) in _CMP for o in n.ops):
            raise bad("comparison operator not allowed")
        for v in [n.left, *n.comparators]:
            _check(v, names, params, src)
    elif isinstance(n, ast.BinOp):
        if type(n.op) not in _BIN:
            raise bad(f"operator {type(n.op).__name__} not allowed")
        _check(n.left, names, params, src)
        _check(n.right, names, params, src)
    elif isinstance(n, ast.Constant):
        if type(n.value) not in (int, float):
            raise bad(f"constant {n.value!r} not allowed")
    elif isinstance(n, ast.Name):
        if n.id not in names:
            raise bad(f"unknown name {n.id!r}")
    elif isinstance(n, ast.Attribute):
        if not (isinstance(n.value, ast.Name) and n.value.id == "p" and n.attr in params):
            raise bad("attribute access other than p.<param>")
    elif isinstance(n, ast.Call):
        if not (isinstance(n.func, ast.Name) and n.func.id in _CALLS) or n.keywords or not n.args:
            raise bad("only abs/min/max calls with positional args")
        if n.func.id == "abs" and len(n.args) != 1:
            raise bad("abs takes one argument")
        if n.func.id != "abs" and len(n.args) < 2:
            raise bad(f"{n.func.id} needs >= 2 arguments")
        for a in n.args:
            if isinstance(a, ast.Starred):
                raise bad("starred args")
            _check(a, names, params, src)
    else:
        raise bad(f"{type(n).__name__} not allowed")


def eval_expr(tree: ast.Expression, env: dict, params: dict):
    """Vectorised evaluation of a checked tree over numpy arrays. NaN comparisons are False."""
    def ev(n):
        if isinstance(n, ast.BoolOp):
            f = np.logical_and if isinstance(n.op, ast.And) else np.logical_or
            return _fold(f, [_bool(ev(v)) for v in n.values])
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return np.logical_not(_bool(v)) if isinstance(n.op, ast.Not) else (-v if isinstance(n.op, ast.USub) else +v)
        if isinstance(n, ast.Compare):
            out, left = True, ev(n.left)
            for op, c in zip(n.ops, n.comparators):
                right = ev(c)
                out = np.logical_and(out, _CMP[type(op)](left, right))
                left = right
            return out
        if isinstance(n, ast.BinOp):
            with np.errstate(divide="ignore", invalid="ignore"):
                return _BIN[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return env[n.id]
        if isinstance(n, ast.Attribute):
            return params[n.attr]
        if isinstance(n, ast.Call):
            return _CALLS[n.func.id](*[ev(a) for a in n.args])
        raise SpecError(f"{type(n).__name__} not allowed")  # unreachable for checked trees
    return ev(tree.body)


def _bool(v):
    v = np.asarray(v)
    return v if v.dtype == bool else np.nan_to_num(v.astype(float)) != 0


def expr_names(tree: ast.Expression) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in _CALLS and n.id != "p"}


# ---- loading ---------------------------------------------------------------------------------
REQUIRED = ("id", "instrument", "session", "features", "entry", "exit", "sizing")


def _num(d, k, what, required=True, positive=True):
    v = d.get(k)
    if v is None and not required:
        return
    if not isinstance(v, (int, float)) or isinstance(v, bool) or (positive and v <= 0):
        raise SpecError(f"{what}.{k} must be a positive number")


def load_spec(path) -> Spec:
    try:
        raw = yaml.safe_load(Path(path).read_text())
    except (OSError, yaml.YAMLError) as e:
        raise SpecError(f"cannot read spec {path}: {e}") from None
    if not isinstance(raw, dict):
        raise SpecError("spec must be a mapping")
    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise SpecError(f"missing keys: {missing}")
    for k in ("instrument", "session", "features", "entry", "exit", "sizing", "card", "params", "grid"):
        if not isinstance(raw.get(k, {}), dict):
            raise SpecError(f"{k} must be a mapping")
    s = Spec(**{k: raw[k] for k in REQUIRED}, card=raw.get("card") or {}, params=raw.get("params") or {},
             grid=raw.get("grid") or {}, raw=raw, path=str(path))
    _validate(s)
    return s


def _validate(s: Spec) -> None:
    if not isinstance(s.id, str) or not s.id:
        raise SpecError("id must be a non-empty string")
    if s.instrument.get("segment") not in SEGMENTS:
        raise SpecError(f"instrument.segment must be one of {sorted(SEGMENTS)}")
    if not isinstance(s.instrument.get("underlying"), str):
        raise SpecError("instrument.underlying must be a string")
    start = parse_hhmm(s.session.get("start"), "session.start")
    end = parse_hhmm(s.session.get("end"), "session.end")
    if start >= end:
        raise SpecError("session.start must be before session.end")
    if "time" in s.exit:
        parse_hhmm(s.exit["time"], "exit.time")
    _num(s.exit, "stop_atr", "exit")
    _num(s.exit, "take_atr", "exit", required=False)
    if not isinstance(s.exit.get("atr_days", 14), int) or s.exit.get("atr_days", 14) < 1:
        raise SpecError("exit.atr_days must be a positive int")
    for k in ("risk_per_trade", "capital_inr", "max_lots"):
        _num(s.sizing, k, "sizing")
    for k, v in s.params.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise SpecError(f"params.{k} must be a number")

    for name, args in s.features.items():
        if not str(name).isidentifier() or name in ("p", *_CALLS):
            raise SpecError(f"bad feature name {name!r}")
        if not isinstance(args, dict):
            raise SpecError(f"features.{name} must be a mapping")
        try:
            features.validate(args)
        except (ValueError, TypeError) as e:
            raise SpecError(f"features.{name}: {e}") from None
    _check_cycles(s)

    if not ({"long", "short"} & set(s.entry)):
        raise SpecError("entry needs long and/or short")
    for side in ("long", "short"):
        if side in s.entry:
            compile_expr(s.entry[side], s.names, set(s.params))
    mpd = s.entry.get("max_per_day")
    if mpd is not None and (not isinstance(mpd, int) or isinstance(mpd, bool) or mpd < 1):
        raise SpecError("entry.max_per_day must be a positive int")

    if len(s.grid) > 2:
        raise SpecError("grid: at most 2 params")
    for k, vals in s.grid.items():
        if k not in s.params:
            raise SpecError(f"grid.{k} is not a param")
        if not isinstance(vals, list) or len(vals) != 3:
            raise SpecError(f"grid.{k} must list exactly 3 values")
        if vals[1] != s.params[k]:
            raise SpecError(f"grid.{k}: centre value {vals[1]} must equal params.{k} = {s.params[k]}")


def _check_cycles(s: Spec) -> None:
    names = s.names

    def visit(n, stack):
        if n in stack:
            raise SpecError(f"feature cycle: {' -> '.join([*stack, n])}")
        for d in features.deps(s.features.get(n, {})):
            if d not in names:
                raise SpecError(f"features.{n}: unknown feature {d!r}")
            visit(d, [*stack, n])

    for n in s.features:
        visit(n, [])


def _canonical(raw: dict) -> str:
    return yaml.safe_dump(raw, sort_keys=True, default_flow_style=False, allow_unicode=True)


def spec_sha(path) -> str:
    return hashlib.sha256(_canonical(yaml.safe_load(Path(path).read_text())).encode()).hexdigest()


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def spec_diff(a: Spec, b: Spec) -> list[str]:
    """Dotted paths whose values differ, ignoring id and card."""
    fa, fb = (_flatten({k: v for k, v in s.raw.items() if k not in ("id", "card")}) for s in (a, b))
    return sorted(k for k in fa.keys() | fb.keys() if fa.get(k, _MISSING) != fb.get(k, _MISSING))


_MISSING = object()
