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
2. `INSTANCE_DIR/instance.toml`: `brand`, `agent_name`, `trusted_proxies`, `pages_disabled`, `accordion_nav`, `avatar_presets`, `bootstrap_user`, `[login]`, `[roles]`, `[redirects]` (old address = current address, so links in old notifications and bookmarks still land on the right page).
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

An install can also add scripts and styles to a page that already exists, without replacing it: `{ "extend": "settings", "scripts": ["/instance/static/more.js"] }` in `pages.d`. The page decides what an added script may do. Settings lets a script add a section (or replace a core one) with `window.__APP_SETTINGS_SECTIONS__.push({ id, label, icon, note, parts: [Component], replaces: "model" })`; the mail backend lets an install add fields to an opened message through `mail_backend.READ_HOOKS`; the chat page reads `window.__ATRIUM_UI__` (the avatar script supplies the drawn `Orb`, an install may supply an `AvatarHero`).
Optional fields: `group` and `subgroup` place the page in a sidebar section and folder, `order` sorts it, `aliases` are old addresses that redirect to it, and `"nav": "gear"` takes the page out of the sidebar list and reaches it from the gear icon at the bottom of the sidebar instead (Settings uses it; that page also holds the Sign out button). A user who cannot open the gear page keeps a Sign out button in the sidebar.

`"requires": "mail"` or `"requires": "nextcloud"` lists the page, and serves it, only when that service is configured (the Email pages need `MAIL_*`; Files and Calendar need `NEXTCLOUD_*`; see `config.private.example`). Their routes (`/api/mail`, `/api/nextcloud`) are mounted under the same rule, always behind the owner permission.

### Email, Files and Calendar

**Personal mailboxes** (`MAIL_USER_ACCOUNTS=1`, off by default). The Email pages then show for everyone who may use the app. A person without a mailbox sees a **Connect your email** form: a provider (Gmail, Yahoo, Fastmail, Zoho) or their own IMAP and SMTP server names, their address, and an app password. Under the password field the form links to the provider's own page where an app password is made (`help_url` in `mail_accounts.PRESETS`, opened in a new tab; not shown for Other) with a few plain steps for that provider, and a normal password refused by a listed provider gets the message "That provider needs an app password, not your normal password." with the same link (a server that cannot be reached is reported separately, and nothing a person typed is ever echoed). Atrium signs in to both servers once to check, then keeps the password encrypted in `<data>/mail-accounts.json` (AES-GCM, bound to that person and address). Set `MAIL_ACCOUNTS_KEY` (at least 32 random characters, for example `python -c "import secrets; print(secrets.token_urlsafe(48))"`) in the private settings so the key lives apart from the data folder: a copy of the data folder or its backups then opens nothing. Without it a key file `mail-accounts.key` (mode 600) is made beside the data, which protects only against the saved file leaking on its own. When the setting is added later, logins saved under the key file are re-sealed under it at the next start and the key file is destroyed; keep the key safe, because logins cannot be read without it (people would connect again). The browser is never sent it back, and a person who is removed from the user list loses their saved mailboxes at the next start. **A person can connect up to 5 mailboxes** (one per address; Add another mailbox in the Email pages) and switch between them with the dropdown above the list; each request names the open mailbox (`?account=<id>`), so two tabs can show two mailboxes, and the id is only ever looked up among that person's own mailboxes (taken from their session), so another person's id opens nothing. **Disconnect** removes the open one. An owner who has the server-wide mailbox opens on it, sees it in the dropdown as the Server mailbox, and may add personal mailboxes next to it; everyone else never sees it. Routes an install adds (such as the block list) always act on the server-wide mailbox, whichever mailbox the page has open, and the page shows them only while it is open. Mailboxes saved before several were allowed are read as a list of one and rewritten in the new shape at the next save; an older Atrium cannot read the new shape, so a rollback to one loses saved mailboxes. Non-owners may reach only the core mail routes, matched exactly by method and whole path; any route an install adds to `/api/mail` (for example a block-list action such as `POST /messages/<uid>/spam`, which acts on the server-wide mailbox) stays owner-only. Mail servers are reached over verified implicit TLS only (IMAP 993, SMTP 465). Because people type the server names, a name must be a public DNS name (no IP addresses, no one-word or internal names), every address it resolves to must be public (no private, loopback, link-local, multicast or reserved ranges), and the connection goes to that checked address while the certificate is still verified against the typed name, so a name that changes its answer cannot redirect it inward. Connection attempts are limited to 6 per person per 10 minutes. The agent never sees anyone's mail. Outlook.com, Microsoft 365 and iCloud only offer SMTP with STARTTLS, or require OAuth, so they are not offered yet.

Email talks IMAP and SMTP to one mailbox and always verifies the server certificate (implicit TLS only; there is no setting to turn that off). If you reach the server by an address that is not on its certificate, set `MAIL_IMAP_TLS_SERVER_NAME` and `MAIL_SMTP_TLS_SERVER_NAME`. Moving and deleting act on the chosen message only, a message is never cut short (one over 64 MB is refused with a clear error), and every Send carries a one-time `submission_id`, so retrying a send never delivers it twice.

Files and Calendar use WebDAV and CalDAV on a Nextcloud with an app password. An upload never replaces a file of the same name unless the request says `replace=1`; Nextcloud keeps the old version.

A downstream install can add routes of its own to `/api/mail` (for example a server-wide block list) and list them in `mail_backend.FEATURES`; the page shows the matching controls (`"blocklist"` turns on the Spam block menu).

### Agentic coding: projects, sessions, the idea pipeline and the shared browser

Coding sessions belong to the project they were started in; older sessions that were never tied to a project show in every project. A project can be linked to a kanban card (`PUT /api/code/links`), and that card's worker runs and subagents then appear under the project as read-only related work (the server never continues them; sending from one starts a fresh session). Set `HERMES_KANBAN_WORKSPACES` if the agent's container keeps card workspaces somewhere other than `/opt/data/kanban/workspaces`.

Every idea in the pipeline belongs to a project. In the default project (`idea-pipeline`, the incubator) a build gets a coding project of its own. In any other project the idea is built inside it, and only after a restore point was saved: if the restore point fails, the build does not start. Advancing a stage is safe to retry: a retry adopts the stage's card and the build project an interrupted attempt already made, and "Approve everything" shows on the idea why it stopped instead of stalling silently.

Restore points in the sidecar get unique names, and a rewind is checked against the checkpoint's contents before it reports success (a checkpoint of the present is kept, so a rewind can be undone). The terminal closes within a few seconds if the person's owner access is withdrawn.

The agent's shared browser has no open debugging port. The sidecar publishes `GET /json/version` and the `/devtools/browser/<id>` WebSocket on its private port 9000, and both require the `X-CDP-Token` header to equal the sidecar's `CDP_TOKEN`; with `CDP_TOKEN` unset the entry point is closed. The agent's CDP client must be able to send that header. Check this before upgrading an install whose agent connected to the old port 9222.

### Security notes from the audit

- **Sessions.** A sign-in is tied to the password it was made with. If that password changes, even from the command-line tool while the app is running, the old sign-in stops working at its next request; sign-ins from before this check are still accepted until they expire. The sessions file is shared by the app and the command-line tool under a file lock and read fresh each time, so one cannot undo the other's change; a damaged file means everyone signs in again, never a broken app.
- **Chats and runs.** A chat is started only when Hermes says it does not exist yet. If Hermes cannot say (an error or no answer), the request is refused with a "could not be verified" message and nothing is started or claimed. A run is shown only to the person whose chat it belongs to, and a run whose owner cannot be determined is refused, not shown to everyone. Owners are not slowed by these lookups.
- **Approval records.** When an approval service is set up, the list of proposed actions and the log (which can include proposed mail, subjects, senders and file details), and deciding or undoing them, are for owners only.
- **Uploads.** The file-type check reads only the first bytes of a file. The chat reads PDF, Word, Excel and PowerPoint files with `pypdf`, `python-docx`, `openpyxl` and `python-pptx`, which are in `requirements.txt` and, with their dependencies, in the hash-pinned `requirements-source.lock`.
- **Host updates.** Package updates report a failed package-list refresh as an error and never as "nothing is waiting". An update that leaves a previously running container missing is marked failed. A restore point is forgotten only once the previous version is verified running again; a rollback checks that the restored containers run the saved image, keeps its restore point if that check fails, and works even when every container of the group is down. Restore points of a group that is not running are still listed with their Roll back button.

### The avatar: reactions, tool icons and looks

The character notices what happens (`static/pages/avatar/reactions.js`, used by the Live agent page): you send a message, work starts, an approval is needed, a job finishes, a failure, an answer arrives, you come back after more than five minutes away. One failure is a wince; failing again within ten minutes makes her more fed up each time (up to a limit), and an answer arriving clears the count. A reaction can never break the page: a missing or failing character is ignored.

While the agent works, the icon for the tool it is really using glows: the magnet on the 3D character's whiteboard and the matching icon around the drawn avatar. `GET /api/tool-activity` (owners only) gives the agent's latest tool call as a name, a kind (search, web, docker, git, db, python, terminal, mail, clock, vault, code) and a time, read from the agent's most recent sessions that have messages (the empty scheduled check-ins are skipped), cached for 3 seconds so every open page costs one look at Hermes. It never returns arguments. `toolsignal.js` asks only while the agent is working and the tab is visible, shares one timer between everything on the page, and the glow lasts 25 seconds after the call.

An install can offer its own looks in Settings > Avatar with `[[avatar_presets]]` in `instance.toml`: each has a `name` (up to 40 characters) and a `look` table of plain values (text, numbers, true or false; up to 24 keys). A list that is not in that shape is ignored as a whole, never half used.

### Chat: approvals and read-aloud

When the agent needs a yes, the chat shows one approval card per request (`static/pages/chat/approval.js`). It shows the **full** command, never a cut-down one; offers "Approve once" only when there is a command to approve, and "Deny" whenever the request can be answered; shows a request that has no id, or is a question and not an approval, without buttons (it cannot be answered from the card); and sends one answer per click. The answer goes to the run the card was shown for and is refused if that run is no longer the current one, so a stale card cannot approve something in a later run. A failure is shown and the buttons work again.

Read-aloud can speak each sentence as soon as it has printed, while the rest of the answer is still arriving (`static/pages/chat/livespeech.js` decides where to cut: after a complete sentence, never inside a code fence or `code span`, and not for tiny pieces). It uses the same speech service as the "Listen" button and falls back to speaking the whole answer when streaming is not possible. Both helpers are plain scripts that the page reads at start-up, tested in `tests/chat-approval.test.js` and `tests/chat-live.test.js`.

### Your own chat model

An owner can choose, under Settings > Chat model, which model answers their chats: the install's default, or a model from a cloud provider that is already connected to the agent (`GET /api/plugins/atrium-settings/models` lists only connected cloud providers; local, user-defined and unconnected ones are left out). The choice is saved per person (a file named by a hash of the user name) and applies to their chats, their Home ask box and the cloud notice on uploads. It is sent to Hermes as a locked model (`require_model_lock`), and choosing a cloud model needs an explicit yes because the chat text goes to that provider. Everyone else keeps the install's default; the install-wide engine switch on Home still applies to people who have not chosen. A file that was damaged or edited by hand is ignored, never trusted. Agentic coding runs keep using the install-wide engine.

An install that manages its own local models can set `chat_model.LOCAL_ROUTE` to a function `route(personal) -> dict` that supplies the run fields for its default model (it may raise `HTTPException`, for example 503 while a model server is switching).

### Sidebar

On wide screens a button in the sidebar head collapses it to an icon rail (every entry keeps its name as a hover title and an accessible label); the choice is remembered in the browser. On phones the sidebar is a drawer, as before. Set `accordion_nav = true` in `instance.toml` for accordion sections: only the section holding the current page starts open, opening one section (or folder) closes its siblings, and open sections are not remembered between pages. Without it, sections open and close independently and are remembered, as before. The behaviour is covered by `tests/nav.test.js` (needs `jsdom`: `NODE_PATH=<folder with node_modules> node tests/nav.test.js`).

### Color themes

Settings > Appearance lets every person (not only owners) pick a color theme: Abyss (the original look), Midnight, Ember, Forest, Rose, Graphite and High contrast. The choice is saved per person in the users file and put on `<html data-theme="...">` by the server, so the first paint already has it. A theme only changes the design tokens in `static/tokens.css` (`themes.css` has one block per theme, all dark); pages use `var(--color-primary)`, `rgba(var(--primary-rgb), .2)`, `var(--surface-deep)` and so on instead of hard-coded colors, which is how every page follows the theme. The `*-rgb` tokens are plain `r, g, b` triplets so a page can still set an alpha. An install's own `instance/theme.css` loads last and can still override any token for everyone. To add a theme, add its entry to `app/themes.py` and a block to `themes.css` with the same tokens; `tests/test_themes.py` checks that every theme is complete, that its triplets match its colors, that text meets WCAG AA contrast, and that no page stylesheet hard-codes a brand color again.


## The built-in pages

- **Home** (`/`): the agent and the work on one screen. A greeting, an overall health ring (for roles that may see infrastructure), a single "Needs you" list (failed jobs, blocked cards, services down, backups, security), today's focus,
  the idea pipeline, the radar, the daily log, weather and river levels, scheduled jobs, usage and recent chats.
- Home says in one sentence what the agent is working on right now (a running board card first, then a running scheduled job, then "Answering a message."; nothing when idle). An open chat or command-line task that spoke in the last 90 seconds counts as work; the agent's own scheduled check-in sessions never do. Under the hero are KPI tiles (model calls with a trend, chats, tokens, jobs, GPU memory when known), and the cards use small plain-SVG charts: a smooth area chart for usage, ring gauges for the systems, a funnel for the idea pipeline, icons for weather. A "Coming up" card shows the next week of the connected calendar and is hidden when no Nextcloud is configured. A radar line may end with its link (`... | why it matters https://...`); only a trailing web link is taken as the link. Motion respects `prefers-reduced-motion`.
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

- **Memory** (a section of Settings, owners only; `/memory` redirects to Settings): the agent's notes (an Obsidian-compatible vault) as a graph, with search and a note reader. Owners can add, edit and delete notes where the folder is mounted writable (a deleted note moves to a `.trash` folder beside it); everywhere else a note says it is read-only.
- **Skills** (a section of Settings, owners only): the agent's skills, one `SKILL.md` per folder under `<data>/skills` (optionally inside a category folder). Owners can read, add, change and delete them; a skill must keep a header with a `name:` and a `description:`, and a deleted skill moves to `skills/.trash`. The agent uses a change from her next session. The section needs the skills folder mounted into the app (writable to edit, `:ro` to only read); without it the section says there is no skills folder.
- **Workflows** (`/workflows`, Work): the scheduled jobs in plain words with run now, pause and resume and the last runs of each; the topics the
  nightly radar searches for (editable); and what the agent did by herself.
- **Settings** (`/settings`): which pings may buzz a phone and quiet hours, when each job runs, and the subscribed devices.

Jobs are reached through the Hermes dashboard's job routes (the `hermes` command only exists inside Hermes' container), so the app needs the
dashboard token and nothing else for them. Editing the radar topics needs the vault's `Radar` folder to be writable and mounted writable;
the rest of the vault stays read-only. A job's "last answer" is read from its output files when the app may open them, and otherwise from the final reply of the job's last run (through the dashboard).

## Connections (external tools for the agent)

Settings > Connections (owners only) lists Hermes's MCP catalog. Tools that need an API key take it in a form. Tools that sign in with OAuth show a **Sign in** button: Atrium asks Hermes to start the flow (`POST /api/mcp/servers/<name>/auth`, installing the entry first if needed), opens the provider's page in a new tab and watches the flow until Hermes reports it approved, then lists the tools. Hermes does the whole exchange and keeps the token; no code or token passes through Atrium, and Atrium only ever passes on an `https` sign-in address. The provider redirects the browser to Hermes's own callback (`<HERMES_DASHBOARD_PUBLIC_URL>/api/mcp/oauth/callback/<name>`), so that address must be reachable from the owner's browser. Entries that need an app registered with the provider first (they declare `required_env`), or that need local setup, stay blocked with a note. Needs a Hermes whose dashboard has the OAuth flow routes (v0.23 has them); older ones answer that the operation is unavailable.

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
- **Containers.** The same section lists the install's Docker Compose groups, whether a newer image exists for each (a new build of a floating tag, or a
  newer version within the same major version of a pinned one), and lets an owner update one group at a time. The helper saves a restore point first,
  pulls and recreates only that group, waits for it to be healthy, and rolls back by itself if it is not. **Roll back** puts the previous build back
  for as long as the restore point is kept. Every group can be updated here; groups carry a low, medium or high risk label
  (`HOSTCTL_TIER_LOW`, `HOSTCTL_TIER_MEDIUM`, everything else high) and a high-risk update shows a warning before it runs. Routes: `GET /api/host/containers`, `POST /api/host/containers/check|update|rollback`. See `host/README.md`.
- Home lists "N updates waiting" and "N container updates available" and points at this section when it is set up. A shutdown leaves the machine off until someone presses its power button.
