#!/usr/bin/env bash
# Set up a local Qwen model with Ollama for the browser agent.
#
#   ./setup.sh                 pick a model for this machine's memory, install, test
#   ./setup.sh --model qwen3.5:9b
#   ./setup.sh --apply         ...and switch backend/.env to the local model
#
# Steps: install Ollama (Homebrew) -> start it tuned for long contexts ->
# download the model -> check it can drive the agent's tools -> optionally
# point the backend at it (backend/.env is backed up first).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$(cd "$HERE/../backend" && pwd)"

MODEL=""
APPLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

# --- Memory -> model size ---------------------------------------------------
if [[ "$(uname)" == "Darwin" ]]; then
  RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
else
  RAM_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1048576 ))
fi

# Leave room for Chrome and the context window: the model should use well
# under half of memory on small machines.
if   (( RAM_GB <= 12 )); then AUTO_MODEL="qwen3.5:4b";  CONTEXT=32768; PAGE_TEXT=16000; ELEMENTS=100
elif (( RAM_GB <= 24 )); then AUTO_MODEL="qwen3.5:9b";  CONTEXT=32768; PAGE_TEXT=30000; ELEMENTS=150
elif (( RAM_GB <= 48 )); then AUTO_MODEL="qwen3.5:27b"; CONTEXT=65536; PAGE_TEXT=40000; ELEMENTS=150
else                          AUTO_MODEL="qwen3.5:35b"; CONTEXT=65536; PAGE_TEXT=40000; ELEMENTS=150
fi
MODEL="${MODEL:-$AUTO_MODEL}"
echo "==> ${RAM_GB} GB memory: using ${MODEL} with a ${CONTEXT}-token context"

# --- 1. Install Ollama --------------------------------------------------------
if ! command -v ollama >/dev/null; then
  if command -v brew >/dev/null; then
    echo "==> Installing Ollama with Homebrew"
    brew install ollama
  else
    echo "Ollama is not installed. Install it from https://ollama.com/download, then rerun." >&2
    exit 1
  fi
fi
echo "==> $(ollama --version 2>/dev/null | tail -1)"

# --- 2. Start the server -------------------------------------------------------
"$HERE/start.sh"

# --- 3. Download the model -----------------------------------------------------
echo "==> Downloading ${MODEL} (skipped if already present)"
ollama pull "$MODEL"

# --- 4. Check it can drive the agent ------------------------------------------
echo "==> Checking tool calling"
(cd "$BACKEND" && uv run python "$HERE/check_model.py" --model "$MODEL" --context "$CONTEXT")

# --- 5. Point the backend at it ------------------------------------------------
if (( APPLY )); then
  ENV_FILE="$BACKEND/.env"
  [[ -f "$ENV_FILE" ]] || cp "$BACKEND/.env.example" "$ENV_FILE"
  BACKUP="$ENV_FILE.backup-$(date +%Y%m%d-%H%M%S)"
  cp "$ENV_FILE" "$BACKUP"
  python3 - "$ENV_FILE" "$MODEL" "$CONTEXT" "$PAGE_TEXT" "$ELEMENTS" <<'PY'
import re, sys
path, model, context, page_text, elements = sys.argv[1:]
values = {
    "LLM_PROVIDER": "ollama",
    "LLM_MODEL": model,
    "LLM_CONTEXT_WINDOW": context,
    "AGENT_PAGE_TEXT_LIMIT": page_text,
    "AGENT_ELEMENT_LIMIT": elements,
}
lines = open(path).read().splitlines()
for key, value in values.items():
    pattern = re.compile(rf"^#?\s*{key}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
open(path, "w").write("\n".join(lines) + "\n")
PY
  echo "==> backend/.env now uses ${MODEL} (previous file saved as $(basename "$BACKUP"))."
  echo "    Restart the backend: cd backend && uv run python -m app"
else
  cat <<MSG

Done. To use the local model, set these in backend/.env and restart the backend
(or rerun this script with --apply):

  LLM_PROVIDER=ollama
  LLM_MODEL=${MODEL}
  LLM_CONTEXT_WINDOW=${CONTEXT}
  AGENT_PAGE_TEXT_LIMIT=${PAGE_TEXT}
  AGENT_ELEMENT_LIMIT=${ELEMENTS}
MSG
fi
