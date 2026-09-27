# Local LLM: Qwen on Ollama

Run the browser agent on a model on your own computer instead of a cloud API. This means no API key, no usage quota and no per-request cost, and pages you ask about never leave your machine.

The backend talks to [Ollama](https://ollama.com) through LangChain (`LLM_PROVIDER=ollama`). The recommended models are **Qwen 3.5**: they support tool calling, which the agent needs, and they come in sizes small enough for a laptop.

## Quick start

```bash
cd local-llm
./setup.sh --apply
```

That command:

1. installs Ollama (Homebrew),
2. starts it with memory-saving settings (`start.sh`),
3. downloads the Qwen model that suits your machine,
4. checks that the model can drive the agent's tools (`check_model.py`),
5. with `--apply`, switches `backend/.env` to the local model (the old file is backed up next to it).

Then restart the backend (`cd backend && uv run python -m app`). The panel's settings show the model it's using.

Leave out `--apply` to only install and test. Pass `--model <tag>` to choose a model yourself.

## Which model?

What matters is memory: the model, its context window and Chrome all have to fit together. `setup.sh` picks automatically.

| Your Mac/PC memory | Model | Download | Notes |
|---|---|---|---|
| 8 GB | `qwen3.5:4b` | 3.4 GB | Handles reading, summarizing and simple search-and-click tasks. Close other heavy apps. |
| 16 GB | `qwen3.5:9b` | 6.6 GB | Noticeably better at multi-step tasks. |
| 32 GB | `qwen3.5:27b` | 17 GB | Close to cloud quality for most tasks. |
| 64 GB+ | `qwen3.5:35b` | 24 GB | |
| Very tight on memory | `qwen3.5:2b` | 2.7 GB | Reading and summaries only; unreliable at multi-step browsing. |

Newer Qwen generations (`qwen3.6`, `qwen3.8`) currently come only in 27B and larger sizes. On a machine with 32 GB or more, try `./setup.sh --model qwen3.6:27b`.

Smaller local models are slower and make more mistakes than cloud models on long, multi-step tasks. For those, the cloud profile is still the better tool.

## Switching between local and cloud

Edit the top of `backend/.env`, then restart the backend:

```bash
# Local
LLM_PROVIDER=ollama
LLM_MODEL=qwen3.5:4b

# Cloud (your Gemini key stays in LLM_API_KEY; Ollama ignores it)
LLM_PROVIDER=google
LLM_MODEL=gemini-2.5-flash
```

## Settings that matter for local models

| Setting | Default | Why |
|---|---|---|
| `LLM_CONTEXT_WINDOW` | `32768` | How much conversation the model sees. Ollama's own default is small, and it silently drops the start of longer prompts, which is where the task and page text live. So the backend always sets this. |
| `AGENT_PAGE_TEXT_LIMIT` | `40000` (`setup.sh` uses `16000` on 8 GB) | Maximum characters of page text per read. |
| `AGENT_ELEMENT_LIMIT` | `150` (`100` on 8 GB) | Maximum buttons, links and fields per page map. |
| `LLM_THINKING` | `true` | Qwen thinks before answering. That's better on multi-step tasks but slower. Set `false` for faster, simpler tasks. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Where Ollama runs. |

`start.sh` also enables flash attention and an 8-bit KV cache (`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`). Together they roughly halve the memory a long context needs, and they're what let a 4B model hold a 32K context on an 8 GB Mac.

## Everyday use

```bash
./start.sh    # start Ollama (does nothing if it's already running)
./stop.sh     # stop it and free the memory
ollama ps     # what's loaded, and how much memory it uses
ollama list   # downloaded models
ollama rm qwen3.5:9b    # delete a model you no longer need
```

The model stays in memory for 30 minutes after the last request, so consecutive agent steps don't reload it.

## Troubleshooting

- **The panel shows an error right away.** Ollama isn't running: run `./start.sh`.
- **"model not found".** The model in `LLM_MODEL` isn't downloaded: run `ollama pull <model>`.
- **Very slow, or the Mac starts swapping.** The model plus its context doesn't fit. Use a smaller model, or lower `LLM_CONTEXT_WINDOW` to `16384` together with `AGENT_PAGE_TEXT_LIMIT=8000`.
- **The agent answers without doing anything, or loops.** Small models sometimes do. `check_model.py` shows whether tool calling works at all (`cd backend && uv run python ../local-llm/check_model.py --model <tag>`). For hard tasks, use a larger model or the cloud profile.
- **Logs:** `~/Library/Logs/ollama-browser-agent.log`.
