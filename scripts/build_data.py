"""Copy (never link) source data into data/ and write data/BUILD_LOG.md. Idempotent."""
import hashlib
import shutil
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq

QR = Path.home() / "projects/quant/quant-research-v0/qr"
OUT = Path(__file__).resolve().parents[1] / "data"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows(p: Path) -> int:
    if p.suffix == ".parquet":
        return pq.ParquetFile(p).metadata.num_rows
    with open(p) as f:
        return sum(1 for _ in f) - 1


def live_bot_src() -> Path | None:
    db = QR / "research.db"
    if not db.exists():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        r = con.execute("select id from trials where hypothesis_id=0 order by id limit 1").fetchone()
    finally:
        con.close()
    return QR / "runs" / f"{r[0]}.parquet" if r else None


def main() -> None:
    jobs = [
        (QR / "data/processed/fut_front_1m.parquet", OUT / "fut_front_1m.parquet"),
        (QR / "data/raw/index/NIFTY.parquet", OUT / "index/NIFTY.parquet"),
        (QR / "data/raw/options/nifty_options_daily.parquet", OUT / "options/nifty_options_daily.parquet"),
        *[(p, OUT / "vix" / p.name) for p in sorted((QR / "data/raw/vix").glob("vix-*.csv"))],
    ]
    notes = []
    lb = live_bot_src()
    if lb and lb.exists():
        jobs.append((lb, OUT / "refs/live_bot.parquet"))
        cols = ", ".join(f"{f.name} ({f.type})" for f in pq.read_schema(lb))
        notes.append(f"- `refs/live_bot.parquet`: prior project's hypothesis-0 reference bot, trial `{lb.stem}`. Columns: {cols}")
    else:
        notes.append("- `refs/live_bot.parquet`: MISSING (no hypothesis-0 trial found); live-bot ref row omitted.")

    lines = ["# data/ build log", "", "Built by `uv run python scripts/build_data.py`. Files are copies; sources are read-only.", "",
             "| file | source | sha256 | rows |", "|---|---|---|---|"]
    for src, dst in jobs:
        h = sha256(src)
        if not dst.exists() or sha256(dst) != h:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        lines.append(f"| `{dst.relative_to(OUT)}` | `{src}` | `{h}` | {rows(dst)} |")
        print(f"{dst.relative_to(OUT)}: {rows(dst)} rows")
    (OUT / "BUILD_LOG.md").write_text("\n".join(lines + ["", "## Notes", ""] + notes) + "\n")


if __name__ == "__main__":
    main()
