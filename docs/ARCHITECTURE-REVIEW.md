# Architecture review

Written 2026-11-22, completed 2026-11-23. Scope: the standalone layout (`app/`, `host/`, `code-sidecar/`, `tests/`).

**How it was done, and its limits.** I read `main.py`, `auth.py`, `users.py`, `hermes.py`, the shared parts of every `*_backend.py`, the chat
routes, uploads and push code, the memory scan and the mail connection code, and searched the rest by pattern. I measured before and after
where I changed something. I did not read `pages/chat/index.js` (1.7k lines) or `pages/avatar/orb.js` (1.5k lines) line by line, and I did
not review `pages/live/engine.js`, which is a prebuilt third-party bundle (Three.js and TalkingHead, listed in `THIRD_PARTY_NOTICES.md`).
Nothing here was load-tested; the scalability section is reasoning from the code, labelled as such.

## 1. Architecture breakdown

### 1.1 Shape

```
Browser (React shell + one script per page, static/)
   |  HTTPS, session cookie
Reverse proxy (TLS; should also compress, see 2)
   |
ONE uvicorn process: FastAPI app (main.py: make_app)  ----- threads for blocking calls (asyncio.to_thread)
   |-- auth.py / users.py / themes.py     sign-in, roles, sessions, themes (JSON files in DATA_DIR)
   |-- pages.py + pages.json + instance   page manifest: nav, permissions, aliases, install's own pages
   |-- feature routers (mounted under /api/plugins/...)
   |        chat, memory, mission, room, settings, workflows, kanban, connections, model_setup,
   |        mail, nextcloud (files + calendar), code, pipeline, live, hostctl, owner pages
   |
   +--> Hermes API server (:8642)       runs: /v1/runs, events (SSE), approval, stop
   +--> Hermes dashboard                kanban, MCP connections, model setup, chat sessions
   +--> Hermes data folder (files)      agent.json, notes vault, cron jobs, push + notification state
   +--> Mail server (IMAP + SMTP over TLS)          Nextcloud (WebDAV + CalDAV)
   +--> Voice server (TTS)    Local model server (rooms)    Approval service (action gate)
   +--> Code sidecar (separate process: git restore points, terminal, the agent's browser)
   +--> Host helper daemon (server actions: package and container updates, with rollback)
```

State lives in small JSON files written with write-then-rename (`agentkit.atomic_write_text`): in `DATA_DIR` (`users.json`,
`sessions.json`, `chat_owners.json`, `avatar-look.json`, `chat-models/`) and under the agent's data folder (`atrium/agent.json`,
`push.json`, notification preferences, rooms, engine selection). There is no database.

### 1.2 One request, start to finish

1. Middleware adds the security headers (CSP, `nosniff`, `X-Frame-Options`) and `Cache-Control: no-store` for non-static paths.
2. `current(request)` maps the cookie to a session (`sessions.json`) and the session to a user (`users.json`), checking the role is
   valid and that the password has not changed since sign-in.
3. `guard(request, permission, write=..., also=...)` in `main.py` (and the per-router guards in the feature mounts): signed in, then
   role, then an optional extra check (own run, chat ownership), then same-origin for anything that changes data.
4. The handler validates input, calls its upstream (almost always through a worker thread), shapes the answer and returns JSON.

### 1.3 Data flows

- **Sign-in.** `POST /login` (form, 4 KB cap) -> throttle by address and by name -> scrypt check in a worker thread, with a dummy hash
  for unknown names so timing does not reveal them -> session id stored hashed -> `__Host-` cookie (HttpOnly, SameSite=Strict).
- **Page load.** `GET /page` -> the same shell for every page -> `shell-config.js` (the person's role and the pages they may see) ->
  `shim.js` mounts the page script. Home aggregates Hermes dashboard data, the jobs files and the monitors into one JSON.
- **Chat message.** `POST /send` -> chat guard (signed in, role, admin-only paths, ownership claim in `chat_owners.json`) -> size and
  image limits (4 images, 6 MB each, saved to the uploads folder) -> Hermes `POST /v1/runs` with a per-chat session id and the
  person's own model choice -> run id -> the browser opens `/stream/{run_id}` (server-sent events proxied from Hermes) -> the answer
  is shown and read aloud sentence by sentence.
- **Approvals.** A run reports `pending_controls`; the page shows them; `POST /runs/{id}/approval` forwards the owner's choice to
  Hermes. With push enabled, a background task (`chat_push.watch_run`) follows each run for up to two hours and sends a Web Push for
  an approval, a finished answer or an error, subject to preferences and quiet hours (held and sent later as one digest).
- **Memory.** `/graph`, `/note`, `/search` -> one thread walks the notes vault, reusing each note's parsed form while its file stamp
  is unchanged -> links, backlinks, folders -> JSON.
- **Mail.** Every request opens a new verified-TLS IMAP connection, logs in, does its work and logs out (`mail_backend._imap`);
  sends use SMTP over TLS with an idempotent `submission_id`; message HTML is sanitized before it reaches the page.
- **Files and Calendar.** A new `httpx` client per call to Nextcloud (WebDAV and CalDAV); calendar text is parsed and time zones
  resolved (needs time zone data, hence the `tzdata` pin).
- **Code workspace.** `code_backend` checks the same guards, then forwards to the sidecar over HTTP and passes the terminal and the
  agent's browser through as authenticated WebSockets. Sessions belong to projects; ideas are built after a restore point.
- **Server actions.** Settings > Server -> `hostctl_backend` -> the host helper with a shared key and a 20 second timeout; actions
  are tiered by risk, owners only, and container updates roll back by themselves if the new build is unhealthy.
- **Push.** The browser registers a service worker and a subscription (`push.json`, shared with the host side by group); sending
  encrypts per device (RFC 8291) and signs with the server's own VAPID key, through an outbound proxy when one is set.

## 2. Critical problem areas (status after this review)

| # | Problem | Impact | Status |
|---|---------|--------|--------|
| 1 | Every signed-in request parsed `sessions.json` and `users.json` (the first under a file lock), on the event loop. | Latency grew with sessions; every request paid it. | **Fixed.** Re-read only when the file's stamp changed. Measured: 2.6x faster per lookup with 200 live sessions. |
| 2 | A 65-line block copied byte for byte into six backends; the Hermes `.env` reader, API call and run-id check into two or three more. | A fix had to be made six times; copies had started to differ. | **Fixed.** `agentkit.py`; about 520 lines removed. |
| 3 | Memory `/graph`, `/note` (backlinks) and `/search` each read and parsed every note on every request. | Cost grew with the vault; one note view paid for a full scan. | **Fixed.** Per-note cache by file stamp; repeat scan of 1,500 notes 0.12 s; edits still show at once. |
| 4 | About ten pages refreshed every 2.5 to 30 s even in a background tab. | Idle requests to the server and Hermes. | **Fixed** for pure data refreshes (`atriumEvery` in `shim.js`); chat, speech and recording intentionally keep running. |
| 5 | Twelve hand-written atomic-write blocks, some with the permission set after creation. | Inconsistent crash safety; a secret briefly readable. | **Fixed.** One helper; secret files are created `0600`. |
| 6 | `chat_backend.py` mixed chat, uploads, push, notification preferences, voice, icons and approvals (1,423 lines). | Hard to review or test. | **Fixed.** Document reading, icons and all push logic moved out (now 774 lines). Voice moved to `chat_voice.py` on 2026-11-24 (with `voices.py`, which Live now shares instead of keeping its own copy of the voice list); documents moved to `chat_uploads.py` and the approval service to `chat_approvals.py` (2026-11-24). `chat_backend.py` is now about 500 lines: sending, streaming, runs, push, the agent's name, engine and notification settings. |
| 7 | Sign-in, role and origin checks repeated by hand in every `main.py` route. | A new route could forget one. | **Fixed in `main.py`** (`guard()`). The feature mounts keep their own guards (see 11). |
| 8 | **Mail opens a new TLS connection, logs in and out on every request.** | Each mail action pays a TLS handshake and a login; polling or several actions in a row multiply it; mail servers may rate-limit logins. | **Fixed in code** (2026-11-24, `mail_backend._imap`): a finished connection is kept for up to 45 s (2 per mailbox, pinged if idle over 3 s, closed after any error, keyed by mailbox and password) and handed to the next request. Proven with a stand-in server only; check against the real mail server after deploy. |
| 9 | `httpx` clients are created per call in `nextcloud_backend`, `code_backend` and `pipeline_backend`; `hermes.py`, `hostctl`, `live`, `room` use `urllib` in threads. Three HTTP stacks, no connection reuse, different timeouts and error mapping. | Repeated handshakes; inconsistent failure behaviour. | **Partly fixed** (2026-11-24): Nextcloud's short DAV calls now share one client per event loop (rebuilt when settings change); code, REA and pipeline short calls now use `app/httpkit.py` the same way (2026-11-24); uploads, downloads and streams still open their own. **Open (rest):** the `urllib` callers and Mail. Only `hermes.py` and `agentkit.call_sync` talk to Hermes; the others call different services, so merging them is lower value than it looks. |
| 10 | Blocking calls (IMAP, `urllib`, file reads) share the event loop's default thread pool. | Slow upstreams under many users could queue unrelated requests. | **Open**; reasoned from the code, not measured. |
| 11 | Per-router guards (`chatmount`, `code_backend`, `pagesmount`) re-implement the role and origin checks. | Two to four places to keep consistent. | **Mostly fixed** (2026-11-24): `app/guards.py` `role_guard` now serves code, media, REA and the owner pages; chat and mail keep their own because their rules differ. |
| 12 | One process and in-memory state (`last_run` and `owner_of` in `main.py`, the mission and room locks, mission state, the login throttle). | Cannot run more than one worker without changes; state lost on restart. | **Open, documented** (section 3). |
| 13 | `pages/live/engine.js` is 1.1 MB and is sent as it is, uncompressed. | Slow first load of the Live page unless the proxy compresses. | **Open.** Compress in the reverse proxy: a compressing layer inside the app can hold back chat's streamed replies. |
| 14 | About 40 synchronous `read_text` / `json.load` calls in handlers. | Small event-loop stalls under load. | **Open**; none was on a hot path. |
| 15 | The sign-in throttle kept one entry per address or username forever (a flood of made-up names grew memory without limit), and a request with no `Origin` header skipped the origin check. | Slow memory growth under abuse; a small gap for cross-site requests from older clients. | **Fixed** (2026-11-23): `Throttle` forgets stale entries and is capped at 10,000, never dropping an active lockout; a request with no `Origin` is refused when the browser marks it `Sec-Fetch-Site: cross-site`; 3 tests. Left as designed: five wrong passwords for one username lock that account from every address (a tested behavior), so a stranger can lock an account for 15 minutes. |

## 3. Scalability assessment (reasoning from the code; not load-tested)

- **What it is built for:** one install, a handful of people, one agent. For that it is simple and robust: few moving parts, files
  that can be backed up and read by hand, safe writes, and the heavy work (the agent, voice, models) is elsewhere.
- **What breaks first, in order:** (1) mail latency and login churn (problem 8) as soon as several people use Mail at once;
  (2) the shared thread pool (problem 10) when an upstream is slow; (3) per-request file parsing, now fixed for sign-in and Memory;
  (4) the single process itself, only at much larger scale.
- **Why more workers are not safe today:** `uvicorn run:app` starts one process, and several things live in that process: each
  person's last run and run ownership (`main.py`), the mission and room locks, the login throttle and a few caches. Starting more workers
  would give each its own copy, so limits and ownership checks would quietly weaken.
- **Before running more than one worker:** move those few pieces of state into the same file or SQLite store that sessions use,
  replace the JSON stores that are read-modified-written with a store that supports concurrent writers, and pool upstream
  connections (mail, Nextcloud). None of this is needed for the install this was built for.

## 4. Refactoring strategy: what is left, in order of value

1. **Reuse connections.** One shared `httpx.AsyncClient` created at start-up and closed at shutdown for Nextcloud, code and pipeline
   (low risk), then a small, idle-timed IMAP connection reuse for Mail with a liveness check before each use (medium risk: it is
   stateful, and it needs a test against a real mail server, which the synthetic tests here do not provide).
2. **One guard factory** for the feature mounts, so role, ownership and origin are decided in one place.
3. **A dedicated pool** for slow blocking upstreams, or move the `urllib` callers to async `httpx`.
4. **Chat split:** done (voice, uploads, approvals).
5. **Multi-worker readiness** (section 3), only if the install ever needs it.
6. **Front end:** read `chat/index.js` and `avatar/orb.js` closely for leaks and duplicated logic; compress the Live bundle in the proxy.

## 5. What was changed (behaviour preserved)

- `app/agentkit.py` (new): agent-name filling and `AgentRoute`, the Hermes `.env`, API base and key, the Hermes call, the run-id
  check and `atomic_write_text`; used by six backends. The name cache is keyed on the data folder.
- `app/chat_push.py`, `app/docreader.py`, `app/pngicons.py` (new): push and notifications, document previews, home-screen icons.
- `app/main.py`: one `guard()`; `app/auth.py` and `app/users.py`: stamp-based caching; `app/memory_backend.py`: per-note cache.
- `app/static/shim.js`: `atriumEvery`; seven pages use it.
- `requirements.txt`: `tzdata` pinned (makes calendar time zones work on any machine, including ones without a system database).
- `chat_model.save` closes its temporary file before renaming it (Windows cannot rename an open file); the compose example now
  names `deploy/Dockerfile`.

**Checked by:** the full Python suite (354 passed on Linux with `git` available; the only Windows failures are the sidecar tests,
which need `fcntl`, and one file-mode assertion); all JavaScript tests; a drill that ran the old build, the new build and the old
build again over the same data (sign-in cookies, theme, avatar look and Memory carried across both switches); a container
rehearsal with the production restrictions (non-root, read-only root, no capabilities); and read-only checks of the live pages
after the switch. Not checked: a real Hermes behind the test copy, mail send, host-helper actions, approving a real request.

## 6. What this review did not do

- It did not implement problems 8 to 14. Each changes behaviour that needs a real mail server, Nextcloud or load to prove safe, and
  a wrong guess there is worse than the cost it removes.
- It did not review the two large front-end files or the third-party bundle (see the top).
- It measured only what it changed (sign-in lookups, Memory scan); the rest of section 3 is reasoning, not measurement.
