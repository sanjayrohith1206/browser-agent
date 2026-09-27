#!/usr/bin/env bash
# Start the Ollama server tuned for the browser agent (idempotent).
#
# - Flash attention + 8-bit KV cache roughly halve the memory a long context
#   needs, which is what lets a 4B model run a 32K window on an 8 GB Mac.
# - One model and one request at a time: the agent is sequential, and a second
#   loaded model would not fit on small machines anyway.
set -euo pipefail

HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
LOG_DIR="${HOME}/Library/Logs"
LOG="${LOG_DIR}/ollama-browser-agent.log"

if curl -sf "http://${HOST}/api/version" >/dev/null; then
  echo "Ollama is already running at http://${HOST}"
  exit 0
fi

if ! command -v ollama >/dev/null; then
  echo "Ollama is not installed. Run ./setup.sh first." >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
echo "Starting Ollama at http://${HOST} (log: ${LOG})"
OLLAMA_HOST="$HOST" \
OLLAMA_FLASH_ATTENTION=1 \
OLLAMA_KV_CACHE_TYPE=q8_0 \
OLLAMA_MAX_LOADED_MODELS=1 \
OLLAMA_NUM_PARALLEL=1 \
OLLAMA_KEEP_ALIVE=30m \
  nohup ollama serve >>"$LOG" 2>&1 &

for _ in $(seq 1 40); do
  if curl -sf "http://${HOST}/api/version" >/dev/null; then
    echo "Ollama is up."
    exit 0
  fi
  sleep 0.5
done
echo "Ollama did not start. See ${LOG}" >&2
exit 1
