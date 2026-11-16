# Atrium: framework guide

Atrium is a small web app that gives people a private front end for an AI agent. It is the same for everyone. What makes one
install different from another lives in a separate **instance** folder, never in this code.

## The two layers

| Layer | What it holds | Where |
|---|---|---|
| **Framework** | login, users and roles, the shell, page manifests, the design tokens, the Hermes link, tests | this repository |
| **Instance** | this install's name, brand, agent name, proxy addresses, extra pages, theme values, secrets | the folder `INSTANCE_DIR` points to, and the environment |

A test (`tests/test_framework.py::test_no_personal_names_in_the_framework_code`) fails if the framework code ever names a
person, a company or a host.

## Settings, in three layers (a later one wins)

1. Built-in defaults (generic).
2. `INSTANCE_DIR/instance.toml`: `brand`, `agent_name`, `trusted_proxies`, `pages_disabled`, `bootstrap_user`, `[login]`, `[roles]`.
3. Environment: `DATA_DIR`, `INSTANCE_DIR`, `TRUSTED_PROXIES`, `HERMES_API_BASE`, `HERMES_API_KEY`, `HERMES_DASHBOARD_BASE`,
   `HERMES_DASHBOARD_TOKEN`, `HERMES_DATA_DIR`, `OWNER_PASSWORD_HASH` (optional, only to create the first owner).

Copy `instance.example/` to start a new install. Secrets go in `config.private.env` (see `config.private.example`), never in the instance file.

## Users and roles

- People are in `DATA_DIR/users.json` (username, role, password hash; mode 600). Passwords are stored only as scrypt hashes.
- Manage them inside the app container so the files keep the right owner:
  `docker exec -it <container> python /srv/scripts/manage_users.py /data list | add | passwd | role | remove`.
- A role is a list of permissions (`"*"` means all). Defaults: `owner = ["*"]`, `user = ["use"]`. Add roles in `[roles]`.
- Roles are read on every request: removing a user, changing a role or a password takes effect at once and ends their sessions.
- Sign-in is protected by a lockout (per address and per username), a generic error message, and constant-time checks.

## Pages

A page is a script the shim (`static/shim.js`) runs. It registers itself with `window.__HERMES_PLUGINS__.register(name, Component)`
and uses `window.__HERMES_PLUGIN_SDK__` (React, hooks, `fetchJSON`). Pages are declared in manifests:

```json
{ "id": "chat", "path": "/chat", "label": "Chat", "plugin": "chat-page",
  "scripts": ["/static/plugins/chat/index.js"], "styles": [], "permission": "use" }
```

Core pages are in `app/pages.json`; an install adds its own in `INSTANCE_DIR/pages.d/*.json` with scripts under
`INSTANCE_DIR/static/` (served at `/instance/static/`). A user only sees and can only open pages their role allows.
Paths and asset locations are validated; reserved paths cannot be used.
Optional fields: `group` and `subgroup` place the page in a sidebar section and folder, `order` sorts it, `aliases` are old addresses that redirect to it, and `"nav": "gear"` takes the page out of the sidebar list and reaches it from the gear icon at the bottom of the sidebar instead (Settings uses it; that page also holds the Sign out button). A user who cannot open the gear page keeps a Sign out button in the sidebar.

## The built-in pages

- **Home** (`/`): the agent and the work on one screen. A greeting, an overall health ring (for roles that may see infrastructure), a single "Needs you" list (failed jobs, blocked cards, services down, backups, security), today's focus,
  the idea pipeline, the radar, the daily log, weather and river levels, scheduled jobs, usage and recent chats.
- Home also carries, for owners: an engine chip (local or cloud, with a confirmation before cloud use), an Approvals shortcut with its count, a scrolling ticker of the radar headlines, and launcher cards for the main pages.
- **Agentic coding** (`/code`, permission `admin`): a coding workspace with the agent. Left: projects and past sessions. Middle: the conversation, with every tool the agent uses as its own expandable block, approvals when she asks (or a mode that approves everything), and a restore point saved before every message. Right: the project's files (viewable and editable), the changes since a restore point as diffs with a Rewind button, and a shared terminal (xterm.js, a tmux session per project that keeps running when you close the page). It needs the **coding workspace service** in `code-sidecar/` (its own container, internal network only, sharing the agent's workspace folder; the agent and the service use the same user id so each can change the other's files) and the settings `CODE_URL` and `CODE_TOKEN` (a shared secret). The agent is told the project folder through the run's instructions. A **Browser** tab shows one real Chromium that runs in the same service: you see a live picture of the page and your mouse, scroll wheel, keyboard and paste go to it, with tabs and an address bar. The agent drives the very same browser through the browser's debugging port (set `browser.cdp_url` in the agent's config to the service's address, port 9222), so you can watch her work, or step in to log in or click something. The browser profile (logins, cookies) is kept between restarts, and the service has outbound internet for the browser, git, npm and pip. The message box has **slash commands** and **`@file` mentions** (rules in `app/static/pages/code/compose.js`, tested by `tests/compose.test.js`): typing `/` lists the commands (`/help`, `/new`, `/stop`, `/rewind`, `/diff`, `/files`, `/terminal`, `/browser`, `/mode ask|auto`, `/status` act on the page; `/review`, `/commit`, `/test`, `/explain` send a ready-made request to the agent, plus anything typed after them); typing `@` and part of a name offers the project's files (the service's `/files` list), the chosen path stays in the message as `@path`, and the run's instructions tell the agent to read files named that way.
- **Live agent** (`/live`, permission `admin`): the agent on a 3D stage (TalkingHead and Three.js, both MIT, bundled as `static/pages/live/engine.js`). You type, or hold the microphone button (or the Space bar) to talk; speech is transcribed through the same route as Chat. The agent answers through the same API as Chat, and the answer is read aloud with lip-sync from word timings. Mood, gestures and idle life follow what happens. A Customize panel changes scene, camera, voice and behaviour. The character file is the install's own (`atrium/avatar/avatar.glb` in the Hermes data folder) and never ships with the project; clips (`atrium/avatar/clips/*.glb`) and whiteboard notes (the vault's `Projects` folder) are optional. Needs a voice server that returns word timings (`TTS_URL`, Kokoro-compatible). The page security policy allows `wasm-unsafe-eval` and `blob:` fetches for the model decoder.
- **Persona** (a section of Settings, permission `admin`; `/persona` redirects to Settings): edit the agent's persona text (every save keeps a numbered history you can preview and restore)
  and switch the engine between local and cloud (cloud only appears when a key is configured).
- **Avatar** (a section of Settings, permission `admin`; `/avatar` redirects to Settings): choose and customise a drawn character (four built in, drawn on canvas with no image files) with a live preview, ready-made looks, colours, background scenes and picture export. The look is saved on the server (`avatar-look.json` in the data folder; one look for everyone, changed by owners) and drawn in the Home hero.
- Home focus list is editable by admins (saved in the day's note, so the folder holding daily notes must be writable). Everyone else gets a
  minimal Home: the agent's state and a link to Chat.
- **Systems** (`/systems`, permission `admin`): services and websites with their state and an open link, the GPU cluster, CPU, memory and
  storage, the local AI stack, backups, security and mail, and a data-flow summary.
- Both read from Hermes (its dashboard routes and the notes vault) and from the **deck files** an install's own jobs write
  (`DECK_DATA_DIR`: `status.json`, `system.json`, `extra.json`, `weather.json`, `river.json`). Every source is optional; a missing one
  just leaves its part empty. Which services, websites and models are listed comes from `INSTANCE_DIR/systems.json` (see
  `instance.example/systems.json`).
- People see only what their role allows: a plain user gets Home without any infrastructure, and cannot open Systems.
- Users have an optional display name (`manage_users.py ... name USERNAME "Display Name"`) used in the greeting.

## Chat

The **Chat** page (`/chat`, section "Agent") is a full chat with the agent: streaming answers, tool activity, approvals in place, past
chats you can reopen and continue, pictures and documents (PDF, Word, Excel, PowerPoint, text; the agent reads them by path), speaking in
(the agent's own transcription) and read-aloud.

- Its backend is the Atrium chat plugin's, vendored as `app/chat_backend.py` and mounted under the same URLs the page expects
  (`/api/plugins/atrium-chat/...`). The few adaptations are listed at the top of that file. The page itself is the plugin's, unchanged.
- **Per-person chats.** A chat is a Hermes session whose id starts with `atrium-chat-`. The first person to send in an id owns it.
  Plain users only see and change their own chats; owners can see all of them. Chats from before the app existed belong to owners only.
- The dashboard routes the page uses (past chats, rename and delete, transcription, "who am I") are answered by the app with the
  dashboard token, with the same ownership rules. Settings everyone shares (agent name, voice, engine, approvals) are owner-only.
- **Shared files.** The agent is handed uploaded files by path, so the app must see Hermes' data at the same path the agent does
  (`/opt/data`). Run the app with Hermes' group as its main group and make only the `atrium` folder group-writable; mount it writable and
  the rest read-only. See `deploy/compose.example.yaml`.
- **Phone notifications and the installable app** (`PUSH_ENABLED=1`): the page registers a service worker and a manifest, and a phone or
  browser can subscribe to the agent's pings (a run finished, an approval is waiting). Owners only, because the notification store is shared
  by the whole install and carries approvals. The store is `atrium/push.json`; it needs the `cryptography` package. Subscriptions belong to
  an address, so each device subscribes once per address. Sending a push means reaching the browser vendor's push service (for example
  `fcm.googleapis.com` for Chrome and Edge on Android), so an app on a closed network sets `PUSH_PROXY` to an outbound proxy that allows those hosts. Old dashboard links (`/agent-chat`) still land on Chat through a page alias.
- **Approvals** (`ACTION_GATE_URL`): if the install runs an approval service for the agent's proposed actions (send an email, delete a file,
  change her soul), the Chat page lists and decides them. The service must verify who is approving: the app gives it
  `GET /api/gate-check`, which answers 200 only for the session cookie it is shown if that person is an owner (permission `admin`).
  The service replays the approver's cookie to that route and trusts nothing else. Only owners can approve. Without a service the
  pending-actions checks simply answer "none".

## Memory, Workflows and Settings

Three owner-only pages (permission `admin`), each a vendored Atrium Loom plugin whose page is unchanged:

- **Memory** (a section of Settings, owners only; `/memory` redirects to Settings): the agent's notes (an Obsidian-compatible vault) as a graph, with search and a note reader. Read-only.
- **Workflows** (`/workflows`, Work): the scheduled jobs in plain words with run now, pause and resume and the last runs of each; the topics the
  nightly radar searches for (editable); and what the agent did by herself.
- **Settings** (`/settings`): which pings may buzz a phone and quiet hours, when each job runs, and the subscribed devices.

Jobs are reached through the Hermes dashboard's job routes (the `hermes` command only exists inside Hermes' container), so the app needs the
dashboard token and nothing else for them. Editing the radar topics needs the vault's `Radar` folder to be writable and mounted writable;
the rest of the vault stays read-only. A job's "last answer" is read from its output files when the app may open them, and otherwise from the final reply of the job's last run (through the dashboard).

## One design for everyone

All colors, radius and fonts are CSS variables in `static/tokens.css`. Every page uses those variables and nothing else. An install may
change their **values** in `INSTANCE_DIR/theme.css` (loaded last); it cannot change page structure.

## Talking to Hermes

`app/hermes.py` holds the connection: the API server (bearer key), the dashboard server (fixed session token) and a few read-only data
folders. Health is at `/api/health` (owners only; yes/no answers, never a secret). Give the app a private network shared only with Hermes.

## Deploying

`deploy/compose.example.yaml` is a starting point. The container runs as a non-root user with a read-only root file system, no added
capabilities, and no published ports (a reverse proxy reaches it). Keep `INSTANCE_DIR` and secrets out of version control.

## Tests

`pytest tests` for the server (needs `fastapi`, `httpx`, `pytest`) and `node tests/shim.test.js` for the page host (needs `jsdom`).

## Server actions (optional)

**Settings > Server** (owners only) installs package updates, reboots and shuts the machine down. It appears only when the install sets
`HOST_ACTION_URL` and `HOST_ACTION_KEY`, which point at the small **host helper** in `host/` (see `host/README.md`).

- The web app never runs these itself and never holds root. It forwards the request; the helper accepts it only from the listed address,
  with the shared key, and only if the person's own session cookie is accepted by this app's `/api/gate-check` route (owners only).
- Four fixed actions exist: update the safe packages, update the care packages (those named in `HOSTCTL_CARE`, which can restart services),
  reboot, shut down. Nothing else can be asked of the helper.
- Every action first shows what could go wrong right now (a chat run, a running board card, a backup) and needs a "go ahead anyway" tick
  when something is busy. Reboot and shutdown also need a typed word and wait out a countdown that can be cancelled.
- Updates run as their own systemd job, so they survive the app being restarted by the update; the page reports the result and whether a
  restart is still needed. Every action is logged with who and when.
- Routes: `GET /api/host/status`, `POST /api/host/action` (`update_safe`, `update_care`, `reboot`, `shutdown`), `POST /api/host/cancel`.
- Home lists "N updates waiting" and points at this section when it is set up. A shutdown leaves the machine off until someone presses its power button.
