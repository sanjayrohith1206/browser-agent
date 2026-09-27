# Browser Agent

A personal AI assistant that lives in Chrome's side panel. You ask for something in plain language; it plans, works in your browser through tools, observes the result, and continues until the task is done.

**Status: all 5 phases implemented.** The agent reads pages and acts on them, works across tabs, asks you when it needs something only you know, and asks for your approval before anything with real-world consequences (buying, sending, deleting...). Your tasks, history and memory are kept in your own account, encrypted at rest. For example: "Compare the two laptops in my open tabs and tell me which has more RAM", or "Find a vegetarian restaurant near me for Friday and book a table for two". A live timeline shows each step, and the element being used is briefly highlighted on the page. See [Roadmap](#roadmap).

## Architecture

```
Chrome extension (MV3, React, Vite, TypeScript)
  side panel ── sign-in, chat, timeline, questions, approvals, history, memory
  tool executor ── tabs, navigation, screenshots; calls the content script
  content script ── page map + actions (injected on demand)
  background worker ── opens the side panel
        │  REST (bearer token): auth, tasks, history, memory
        │  WebSocket /ws/tasks/:id: events ↓; tool results, answers, approvals ↑
        ▼
Python backend (FastAPI)
  LangGraph agent loop ── agent ⇄ tools until finish_task
  Action policy ── domain restrictions, approvals by risk level
  LangChain chat models ── Anthropic / Google Gemini / OpenAI / Ollama via config
  Browser bridge ── tool calls travel to the extension and back
  Task service ── lifecycle, events, per-user scoping, session locks
        │
        ├── PostgreSQL (or SQLite locally): users, tasks, task_steps, task_events,
        │     agent_messages, memory, browser_sessions — sensitive columns encrypted
        └── Redis (optional locally): sign-in sessions, task locks, rate limits
```

How a task runs:

1. The side panel `POST`s the task, including the current tab, then opens the task's WebSocket.
2. The backend runs a LangGraph loop. The model decides the next tool from what it has observed so far. Nothing is a fixed sequence.
3. Browser tools execute **in the extension**. The backend checks each call against the action policy, sends a `tool_start` event, and the extension runs the tool and replies with a structured `tool_result`.
4. Two tools run in the backend itself: `ask_user` pauses the task (status *waiting*) until you answer in the panel, and `finish_task` ends it with the final answer.
5. Model text (including the final answer as it is written), reasoning summaries and tool progress stream to the panel as events. Every durable event is stored, so a task can be reopened from history.

Shared contracts live in `shared/`. `tools.json` is the single source of tool names, schemas, risk levels and where each tool runs (`runs_in`), and both sides load it. `protocol.ts` defines the wire types; the Python mirror is in `backend/app/agent/events.py` and `backend/app/tools/types.py`.

## Planning, recovery and asking

The system prompt (`backend/app/agent/prompts.py`) asks the model to write a short plan before multi-step work, check the result of every action, and finish with `finish_task` and an outcome (*done*, *partial* or *blocked*). The agent loop backs this up with recovery guidance (`backend/app/agent/recovery.py`), based on how real runs got stuck:

- **Error hints:** failed results carry a `hint` for their error code. For example, stale element IDs ask for fresh IDs, and a closed tab points to `list_tabs`.
- **Repeat detection:** an identical call that fails twice is flagged ("don't repeat it"). So is a run of failures in a row, and re-reading a page that hasn't changed.
- **Step budget:** with two steps left, the agent is told to wrap up. At the limit, only `finish_task` is accepted, so it answers with what it has instead of stopping mid-way.
- **Asking:** `ask_user` shows the question in the panel, with option buttons when the answer is one of a few choices. You can also type any answer. Unanswered questions time out after 15 minutes.

## How the agent sees and uses pages

`get_elements` returns a compact map of the page's interactive elements. Each one has an ID such as `el_k3f_12`, plus its role, accessible name (from `aria-labelledby`, `aria-label`, `<label>`, text, `title` or placeholder), placeholder, current value, link, state (checked, expanded, disabled) and nearby context text. Context is what tells the "Add to cart" button under Laptop A apart from the one under Laptop B. Elements in view come first, and open shadow DOM is included.

A few details:

- **IDs:** an element keeps its ID for the life of the page. The ID includes a per-page token, so an ID from a previous page can never hit a different element after navigation.
- **Search by meaning:** `find_element("search button")` ranks elements using those same signals.
- **Typing** uses the same editing path as real typing (falling back to the React-compatible value setter). Pressing Enter submits the form when the page doesn't handle it itself.
- **After an action**, the extension waits for the page to settle and reports whether it navigated and where to.
- **Screenshots** reach the model as images; they are never stored or sent to the panel.

### Tabs

The task works in one tab at a time. `open_tab` opens a page in a new tab and continues there, `switch_tab` goes back, `list_tabs` shows the window's tabs, and `close_tab` closes one. Links that open a new tab do the same. The task's tabs and current tab are saved on the task. The agent may close tabs it opened; closing one of yours needs your approval.

### Approvals

Before any of these runs, the panel shows what is about to happen and asks you to **Approve** or say **Don't do it**:

- clicks whose label means buying, paying, booking, deleting, sending or publishing, subscribing, or changing account settings (recognized by the extension);
- submitting a form that contains password or card fields;
- closing one of your own tabs;
- any tool whose risk level is listed in `CONFIRM_RISK_LEVELS` (default `high_impact`; set `["interact","high_impact"]` to approve every click and keystroke).

Approval is enforced in the backend. A flagged action comes back as `CONFIRMATION_REQUIRED`, and the backend asks you. Only after you approve does it re-send the call marked `confirmed`, which the model can't set itself. Declining, or not answering within 5 minutes, counts as "no". The agent is told not to retry or work around a no.

The agent **never types into password or payment-card fields**, approved or not. It asks you to fill those in yourself.

## Running it

**Requirements:** Chrome 116+, Node 20+, [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 itself), and an LLM API key (or a local model, see below). PostgreSQL and Redis are optional for local use.

### Backend

```bash
cd backend
cp .env.example .env        # set LLM_API_KEY (and LLM_PROVIDER / LLM_MODEL if not Claude)
uv sync
uv run python -m app        # http://127.0.0.1:8787
```

`GET /healthz` reports whether the model is configured; `GET /readyz` checks the database and Redis.

On first start, the backend creates `backend/data/`, which holds a SQLite database and a generated encryption key (`encryption.key`, readable only by you). Keep that key: without it the stored tasks and memory can't be read. To use PostgreSQL and Redis instead, set `DATABASE_URL` and `REDIS_URL` (see `backend/.env.example`). Migrations run automatically at startup, or by hand with `uv run alembic upgrade head`.

### Extension

```bash
cd extension
npm install
npm run build               # outputs extension/dist
```

Then load it:

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and choose `extension/dist`.
3. Click the toolbar icon to open the side panel. The first time, create your account; this first account can always be created. After that, new accounts need `ALLOW_REGISTRATION=true`.
4. Open any web page and ask *"Read this page and summarize it."*

The 🕘 button shows your past tasks: reopen one to read it again, or delete it. The 🧠 button holds **memory**: short notes the assistant keeps in mind in every task, like preferences or your city. Only you can add or change them; the agent reads them.

To lock the API to your build, copy the extension ID from `chrome://extensions` into `ALLOWED_EXTENSION_IDS` in `backend/.env`. The server address can be changed under ⚙ in the panel.

The toolbar icons are generated from the logo with `extension/scripts/make_icons.py` (see its docstring).

### Tests

```bash
cd backend && uv run pytest && uv run ruff check . && uv run mypy app
cd extension && npm test && npm run typecheck
```

Backend tests use SQLite and an in-process key-value store. To also run them against real servers, set `TEST_DATABASE_URL=postgresql+asyncpg://…` (a scratch database is created per test) and `TEST_REDIS_URL=redis://…`.

What the tests cover:

- **Backend:** the agent loop (tool use, failures, invalid or malformed arguments, step budget, stop, disconnect, refusal, provider errors); `ask_user` and `finish_task`; recovery hints; approvals, domain restrictions and tab state; the tool bridge and provider adapter, including the exact Claude request payload. Also migrations (checked against the schema, down and up), encryption at rest and key rotation, the stores, the key-value store, accounts, sign-in and rate limits, per-user privacy, and REST and WebSocket integration.
- **Extension:** page extraction, the page map and element search, every action and safety guard (under jsdom), approval handling, the tool executor (tabs, injection, navigation, settling, unload-during-click, screenshots, restricted pages), the API client, and the timeline reducer (questions, approvals, history replay).

## Deploying to a server

`docker-compose.yml` runs the backend with PostgreSQL and Redis:

```bash
cp .env.production.example .env.production   # fill in keys, extension ID, host name
docker compose --env-file .env.production up -d --build
```

Put a TLS-terminating proxy in front of port 8787, and point the extension at it under ⚙. With `ENVIRONMENT=production`, the backend won't start without PostgreSQL, Redis, `DATA_ENCRYPTION_KEYS` and `ALLOWED_EXTENSION_IDS`. It also hides the API docs and logs as JSON. The image runs as a non-root user on a read-only filesystem.

## LLM configuration

To run fully on your own machine with Qwen models, no API key needed, see **[local-llm/](local-llm/README.md)**. `./setup.sh --apply` does the whole setup.


| Variable | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `google`, `openai` or `ollama` (local; see [local-llm/](local-llm/README.md)). Any LangChain chat model can be added in `app/llm/factory.py` |
| `LLM_API_KEY` | – | Held only by the backend, never by the extension |
| `LLM_MODEL` | per provider | `claude-opus-5`, `gemini-2.5-flash`, `gpt-5`, `qwen3.5:4b` |
| `LLM_EFFORT` | model default | `low` … `max` (Gemini tops out at `high`) |
| `LLM_THINKING` | `true` | Anthropic and Google: reasoning summaries appear in the timeline. Set `false` for models without it (e.g. Haiku 4.5) |
| `LLM_REFUSAL_FALLBACK` | `true` | Anthropic: server-side refusal fallbacks (`fallbacks: "default"`) |

For Anthropic, requests use automatic prompt caching, so each agent step reuses the cached conversation prefix.

Transient provider failures (rate limits, overloads such as Gemini's 503 "high demand", dropped connections) are retried up to 4 times with backoff. If part of an answer had already streamed, the panel is told to discard it first, so text never appears twice.

## Safety model

- **Accounts:** every API call and WebSocket needs a sign-in token. Passwords are hashed with scrypt. Sessions are random tokens, stored hashed with a 30-day lifetime and revoked on sign-out. Tasks, events, history and memory are scoped to their owner; other users' tasks answer 404.
- **Network:** the backend binds to loopback by default and accepts only `ALLOWED_HOSTS` in the `Host` header (which blocks DNS rebinding). Browser requests must come from the extension's origin. Request bodies are capped at 256 KB, and every response carries security headers.
- **Rate limits:** sign-in attempts (per address and per email), task creation, general requests, WebSocket messages, and running tasks per user.
- **Encryption at rest:** task goals and answers, page URLs and titles, tool inputs, stored events, the agent conversation and memory are encrypted with Fernet before they reach the database. Keys rotate: put the new key first in `DATA_ENCRYPTION_KEYS` and keep the old ones after it. Screenshots are never stored.
- **Approvals and limits:** see [Approvals](#approvals). `ALLOWED_DOMAINS` / `BLOCKED_DOMAINS` stop the agent from opening or using sites, enforced in the backend before any call reaches the browser.
- **Untrusted pages:** page content is treated as untrusted data. The system prompt tells the model never to follow instructions found on pages.
- **Argument checks:** tool arguments are validated against `shared/tools.json` before anything reaches the browser. Malformed or truncated JSON arguments are rejected rather than leniently parsed.
- **Logs:** fields named like passwords, tokens or keys are redacted.

## Known limitations

- **Session lifetime:** a task runs only while its side panel is open. The panel hosts the WebSocket because MV3 service workers are suspended when idle. Closing the panel mid-task marks the task *interrupted*.
- **Restarts:** a task can't continue across a backend restart; tasks in progress are marked *interrupted* at startup. Without Redis, you also have to sign in again after a restart.
- **Synthetic input:** clicks and keystrokes are dispatched by the extension, not by the operating system. A few sites that only accept "trusted" input may ignore them.
- **Frames and windows:** cross-origin iframes aren't read, and the agent works only within the browser window it started in.
- **Memory is yours to edit:** the agent reads memory but doesn't add to it. Tell it things in the task, or add them under 🧠.
- **Gemini free tier:** some free-tier keys allow only 20 requests per model per day, and each task uses about 3–6. When the quota runs out the panel says so. Enable billing, or switch `LLM_MODEL` to another model, since each model has its own quota.
- **Unreadable pages:** Chrome doesn't let extensions script `chrome://` pages, the Chrome Web Store or other extensions. The agent receives a structured `PAGE_NOT_SCRIPTABLE` error and explains this to the user.

## Roadmap

- **Phase 1 (done):** side panel chat, content script, page reading, FastAPI backend, LangChain/LangGraph agent loop, streaming.
- **Phase 2 (done):** normalized page map with element IDs, plus `get_elements`, `find_element`, `get_links`, `click_element`, `type_text`, `clear_input`, `select_option`, `scroll_page`, `navigate`, `wait` and `take_screenshot`. Also on-page action highlights and the interim safety guard.
- **Phase 3 (done):** planning and recovery prompts with recovery hints, repeat detection and a step budget, plus `ask_user` and `finish_task`.
- **Phase 4 (done):** multi-tab tools (`list_tabs`, `open_tab`, `switch_tab`, `close_tab`) and tab state; PostgreSQL (`users`, `tasks`, `task_steps`, `task_events`, `agent_messages`, `memory`, `browser_sessions`, with Alembic migrations); Redis (sessions, locks, rate limits); task history and editable user memory.
- **Phase 5 (done):** approval dialogs for high-impact actions (enforced in the backend from each tool's `risk`, plus actions the extension flags), accounts and per-user authorization, encrypted sensitive data, rate limiting, domain restrictions, and production hardening (production config checks, security headers, request limits, Docker deployment).
