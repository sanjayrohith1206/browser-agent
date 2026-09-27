#!/usr/bin/env bash
# Stop the Ollama server started by start.sh (models are unloaded from memory).
set -euo pipefail
if pkill -f "ollama serve"; then
  echo "Ollama stopped."
else
  echo "Ollama was not running."
fi
