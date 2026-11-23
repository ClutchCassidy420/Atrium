# Architecture review (2026-11-22)

Scope: the standalone layout on `work/port-10` (`app/`, `host/`, `code-sidecar/`, `tests/`). I read `main.py`, `auth.py`,
`hermes.py`, the shared parts of every `*_backend.py`, and searched the rest by pattern. I only skimmed the front end (timers, polling, listeners, structure); `pages/chat/index.js` (1.7k lines) and `pages/avatar/orb.js`
(1.5k lines) were not read line by line. `pages/live/engine.js` is a third-party bundle and was not reviewed.

## 1. Architecture

```
Browser (React shell + per-page JS, static/)
   |  cookie session, same-origin checks
FastAPI app (main.py: make_app)
   |-- auth.py / users.py        scrypt passwords, file-backed sessions, login throttle
   |-- pages.py / pages.json     page manifest -> nav, permissions, aliases
   |-- hermes.py                 thin client for the Hermes API + dashboard (urllib, in a worker thread)
   |-- overview.py               Home page data
   |-- mount_chat / mount_code / mount_owner_pages     feature routers
   |       chat, memory, mission, room, settings, workflows, mail, nextcloud, kanban, pipeline, live, connections, hostctl ...
   |-- code-sidecar/             separate process: git checkpoints, terminal, browser bridge
   `-- host/atrium-hostctl.py    privileged host daemon (updates, model switching)
Hermes (agent runtime): API :8642, dashboard, data dir (agent.json, .env, vault, cron)
```

Data flow for a chat message: page JS -> `POST` (cookie + origin check) -> `deny()` role check -> backend validates and
forwards to Hermes `/v1/runs` -> run id returned -> page streams `/events` (SSE, proxied) -> approvals/stop go back through
the same checks. State lives in small JSON files under the data dir, written with write-then-rename.

## 2. Critical problem areas

| # | Problem | Impact | Status |
|---|---------|--------|--------|
| 1 | **Every authenticated request re-parsed the sessions file** under a file lock, on the event loop (`Sessions.get` via `current()`). | Latency grows with session count; all requests stall behind it. | **Fixed** (stat-stamp cache in `Sessions` and `UserStore`; about 2.6x faster per lookup with 200 live sessions, measured) |
| 2 | A 65-line block (agent-name templating, `AgentRoute`) was **copied byte for byte into 6 backends**; `.env` reading, API base/key and the Hermes call were copied into 2-3 more. | A fix must be made 6 times; they were already drifting (room's `_dotenv` differed). | **Fixed** (`agentkit.py`) |
| 3 | Run-id regex and `public_run` re-implemented in `main.py`, `chat_backend`, `mission_backend`. | Validation drift = security drift. | Regex unified; `public_run` shapes still differ (see 5) |
| 4 | Three HTTP stacks (urllib, httpx, http.client) and several hand-rolled clients (`hermes.py`, chat, mission, room, hostctl, live). | Inconsistent timeouts, error mapping, no connection reuse. | Open |
| 5 | `chat_backend.py` was 1,400 lines mixing chat, uploads/extraction, push (VAPID crypto), notification prefs, voice, icon drawing, actions. | Hard to review or test. | Mostly fixed: document reading (`docreader.py`), PNG drawing (`pngicons.py`) and all push and notification logic (`chat_push.py`: keys, delivery, preferences, quiet hours, run watcher; its `PUSH_ENABLED` and `PUSH_PROXY` settings live there) moved out; `chat_backend.py` is now about 780 lines of routes, chat sending, voice and approvals. |
| 6 | ~12 hand-written atomic-write blocks, ~40 synchronous `read_text`/`json.load` calls inside async handlers. | Small event-loop stalls under load; inconsistent crash safety. | Atomic writes **fixed** (one helper, secrets get 0o600 from creation); blocking reads open |
| 6b | Memory page: `/graph`, `/note` (to find backlinks) and `/search` each read and parsed **every note in the vault** on every request. | Cost grows with the vault; the note page paid for a full scan. | **Fixed** (per-note cache by modification stamp; 1,500 notes: repeat scan 0.12 s, edits still seen at once) |
| 7 | `main.py` repeated `deny(...)` + `same_origin(...)` in every route. | Easy to forget one check on a new route. | **Fixed in `main.py`** (`guard()`); the feature backends still have their own |
| 8 | Module-level mutable state (`last_run`, `owner_of`, `_state`) lives in process memory. | Breaks with more than one worker; lost on restart (acceptable today, a limit for scaling). | Open, documented |
| 9 | Front end: background tabs kept polling the server (about 10 pages refresh every 2.5 to 30 seconds whether or not anyone is looking). `pages/live/engine.js` is **not hand-written**: it is a prebuilt, minified bundle of Three.js and TalkingHead (MIT, listed in `THIRD_PARTY_NOTICES.md`), so its size is a delivery question, not a maintainability one: 1.1 MB, sent by the app as is and uncompressed (only the admin-only Live page loads it). | Wasted requests while idle; slow first load of the Live page when no proxy compresses. | Polling **fixed** for the pure data refreshes (`atriumEvery` in `shim.js`: pauses while the tab is hidden, refreshes once on return; chat, live speech and recording keep working in the background). Compression open: do it in the reverse proxy, not in the app, because a compressing layer in the app can hold back the chat's streamed (SSE) replies. |

## 3. Refactoring strategy (next steps, in order of value and safety)

1. **Dependency/guard helpers in `main.py`**: one `require(who, permission, *, write=False)` that does auth, role and origin
   in one place, so a new route cannot skip a check. Pure refactor; the existing `test_audit_fixes` covers it.
2. **One upstream client** (lower value than it looked: only `hermes.py` and `agentkit.call_sync` talk to Hermes; room, hostctl and live call other services): make `hermes.py` the only Hermes client (async httpx with a shared pooled `AsyncClient`), and have
   `agentkit.call` delegate to it. Removes the thread hop and gives connection reuse.
3. **Finish splitting `chat_backend.py`** (push logic, document reading and icons are out): give voice, approvals and uploads their own routers, mounted by
   `chatmount.py`. No route paths change.
4. **JSON store helper** (`agentkit.atomic_write_text` plus a cached, mtime-stamped reader like the one now in `auth.py`) and
   replace the remaining copies; move blocking reads behind `asyncio.to_thread`.
5. **Scale-out**: if more than one worker is ever wanted, move `last_run`/`owner_of` and the throttle into the same
   file-or-SQLite store the sessions use.

## 4. Code changes made in this pass (behavior preserved)

- `app/agentkit.py` (new): agent name + `{{agent}}` filling, `AgentRoute`, Hermes home/`.env`/API base/key, the Hermes call,
  run-id check, `atomic_write_text`, `save_agent_name`. The name cache is keyed on the data folder.
- `chat`, `memory`, `mission`, `room`, `settings`, `workflows` backends: ~520 duplicated lines replaced by imports.
- `app/main.py`: uses the shared run-id pattern and atomic write.
- `app/auth.py`: `Sessions` re-reads its file only when its (mtime, size, inode) changed, so the CLI and other processes are
  still seen immediately, but the common request path no longer parses JSON.

Verification: full suite before and after: 333 passed in both; the same 8 failures and 13 errors exist before and after
(missing sidecar module, `test_chat_model.py`, one `test_nextcloud.py` case) and were not touched.
