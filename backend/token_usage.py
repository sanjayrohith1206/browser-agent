"""Token usage per task, from the backend log.

uv run python token_usage.py            # last 10 tasks
uv run python token_usage.py --all
"""

from __future__ import annotations

import ast
import re
import sys
from collections import OrderedDict
from pathlib import Path

LOG = Path.home() / "Library" / "Logs" / "browser-agent-backend.log"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STEP = re.compile(r"model_step\s.*?task_id=(\S+).*?usage=(\{.*\})")


def main() -> None:
    if not LOG.exists():
        sys.exit(f"No log at {LOG}. Is the backend running with its output sent there?")
    tasks: OrderedDict[str, dict[str, int]] = OrderedDict()
    for line in LOG.read_text(errors="replace").splitlines():
        match = STEP.search(ANSI.sub("", line))
        if not match:
            continue
        try:
            usage = ast.literal_eval(match.group(2))
        except (ValueError, SyntaxError):
            continue
        t = tasks.setdefault(match.group(1), {"steps": 0, "input": 0, "output": 0})
        t["steps"] += 1
        t["input"] += int(usage.get("input_tokens", 0))
        t["output"] += int(usage.get("output_tokens", 0))

    rows = list(tasks.items()) if "--all" in sys.argv else list(tasks.items())[-10:]
    print(f"{'task':38} {'steps':>5} {'input':>9} {'output':>8} {'total':>9}")
    for task_id, t in rows:
        total = t["input"] + t["output"]
        print(f"{task_id:38} {t['steps']:>5} {t['input']:>9,} {t['output']:>8,} {total:>9,}")
    if rows:
        s_in = sum(t["input"] for _, t in rows)
        s_out = sum(t["output"] for _, t in rows)
        print(f"{'TOTAL':38} {'':>5} {s_in:>9,} {s_out:>8,} {s_in + s_out:>9,}")


if __name__ == "__main__":
    main()
