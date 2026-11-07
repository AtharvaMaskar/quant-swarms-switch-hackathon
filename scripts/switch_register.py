"""Register a custom always_on agent on Switch, once.

    SWITCH_URL=... SWITCH_REG_TOKEN=... uv run python scripts/switch_register.py [name] [description]

Prints the agent id. The API key (returned only once) goes to ./.env (gitignored), never stdout.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qs.switch.client import register  # noqa: E402

ENV = Path(__file__).resolve().parents[1] / ".env"


def save_env(values: dict[str, str]) -> None:
    keep = [ln for ln in (ENV.read_text().splitlines() if ENV.exists() else [])
            if ln.split("=", 1)[0] not in values]
    fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(keep + [f"{k}={v}" for k, v in values.items()]) + "\n")
    os.chmod(ENV, 0o600)


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "orchestrator"
    desc = sys.argv[2] if len(sys.argv) > 2 else "Deflate orchestrator: state machine, budgets, kickoffs, gate runs."
    agent_id, api_key = register(os.environ["SWITCH_URL"], os.environ["SWITCH_REG_TOKEN"], name, desc)
    sfx = "" if name == "orchestrator" else "_" + name.upper().replace("-", "_").replace(".", "_")
    save_env({f"SWITCH_AGENT_ID{sfx}": agent_id, f"SWITCH_API_KEY{sfx}": api_key})
    print(agent_id)


if __name__ == "__main__":
    main()
