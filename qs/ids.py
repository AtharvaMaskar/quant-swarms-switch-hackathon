"""Hypothesis ids: root h12, patch h12v2 / h12.1v2, fork h12.1 / h12.1.2. Family = root."""
import re

_ID = re.compile(r"^(h\d+(?:\.\d+)*)(?:v(\d+))?$")
MAX_FORK_DEPTH = 3


def _parse(hid: str) -> tuple[str, int | None]:
    m = _ID.match(hid)
    if not m or (m[2] is not None and int(m[2]) < 2):
        raise ValueError(f"bad hypothesis id: {hid!r}")
    return m[1], int(m[2]) if m[2] else None


def family(hid: str) -> str:
    return _parse(hid)[0].split(".")[0]


def kind(hid: str) -> str:
    base, v = _parse(hid)
    return "patch" if v else "fork" if "." in base else "root"


def depth(hid: str) -> int:
    return _parse(hid)[0].count(".")


def parent(hid: str) -> str | None:
    """Patch vk -> v(k-1) (v2 -> base); fork -> the id it forked from; root -> None."""
    base, v = _parse(hid)
    if v:
        return base if v == 2 else f"{base}v{v - 1}"
    return base.rsplit(".", 1)[0] if "." in base else None


def _ids(con, fam: str) -> list[str]:
    return [r[0] for r in con.execute("SELECT id FROM hypotheses WHERE family=?", (fam,))]


def next_patch(con, hid: str) -> str:
    base = _parse(hid)[0]
    ks = [_parse(i)[1] for i in _ids(con, family(hid)) if _parse(i)[0] == base and _parse(i)[1]]
    return f"{base}v{max(ks, default=1) + 1}"


def next_fork(con, hid: str) -> str:
    base = _parse(hid)[0]
    if base.count(".") >= MAX_FORK_DEPTH:
        raise ValueError(f"fork depth cap {MAX_FORK_DEPTH} reached for {hid}")
    pat = re.compile(rf"^{re.escape(base)}\.(\d+)(?:v\d+)?$")
    ms = [int(m[1]) for i in _ids(con, family(hid)) if (m := pat.match(i))]
    return f"{base}.{max(ms, default=0) + 1}"


def next_root(con) -> str:
    ns = [int(r[0][1:]) for r in con.execute("SELECT DISTINCT family FROM hypotheses")]
    return f"h{max(ns, default=0) + 1}"


def room_name(hid: str, slug: str) -> str:
    words = re.findall(r"[a-z0-9]+", slug.lower())[:3]
    return "-".join([hid.lower().replace(".", "-"), *words])
