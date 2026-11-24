# Changelog

## Unreleased

- **Personal mailboxes: easier to get an app password.** The Connect form now links, under the password field, to the chosen provider's page for making an app password (Gmail, Yahoo, Fastmail, Zoho; opened in a new tab, hidden for Other) with a few plain steps, and a refused normal password gets a clear message with the same link instead of a raw error. A server that cannot be reached is now reported separately from a refused sign-in. No new settings. Tests: `tests/test_mail_accounts.py`, `tests/mail-connect.test.js`.
- **Personal mailboxes: several per person.** A person can connect up to 5 mailboxes (one per address), switch between them with a dropdown in the Email pages, add another, and disconnect the open one. Each request carries the open mailbox (`?account=<id>`), so tabs are independent and attachment links work; ids are only looked up among the signed-in person's own mailboxes. Owners keep the server-wide mailbox as the default and may add personal ones beside it; routes an install adds (the block list) always act on the server-wide mailbox. `GET /api/mail/status` now lists `accounts` and the `active` one, `PUT /api/mail/account` adds a mailbox, `DELETE /api/mail/account/<id>` removes one. Older single-mailbox saves are converted automatically; an older Atrium cannot read the new shape. The connection attempt limit is now 10 per 10 minutes. Tests: `tests/test_mail_accounts.py`, `tests/mail-connect.test.js`.
- **Personal mailboxes: the encryption key can live in the settings.** Set `MAIL_ACCOUNTS_KEY` (32 or more characters) and the saved mailbox logins are encrypted with a key derived from it (HKDF), so a copy of the data folder or its backups no longer contains the key. Logins saved earlier under the key file are re-sealed at the next start, and the key file is overwritten and removed; if any cannot be opened, nothing changes. A too-short key stops the app from starting with a clear message. Tests: `tests/test_mail_accounts.py`.
- **Fix: personal mailboxes allowed routes by prefix.** With `MAIL_USER_ACCOUNTS=1`, non-owners could reach any route whose path began with `/messages`, including a route an install adds there (the Vera block list's `POST /messages/<uid>/spam`, which acts on the server-wide mailbox). They may now reach only the core mail routes, matched exactly by method and whole path. The feature was off by default and nothing was exposed. Test: `tests/test_mail_accounts.py`.
- **Personal mailboxes for the Email pages** (`MAIL_USER_ACCOUNTS=1`, off by default). Every signed-in person can connect their own mailbox (Gmail, Yahoo, Fastmail, Zoho or their own IMAP and SMTP servers) with an app password, read and send from the Email pages, and disconnect. Passwords are encrypted at rest and never sent back; mail servers must be public names whose every resolved address is public, and the connection is pinned to the checked address with the certificate still verified; attempts are rate-limited; owners keep the server-wide mailbox; routes an install adds to `/api/mail` stay owner-only. New `app/mail_accounts.py`, routes `GET`, `PUT` and `DELETE /api/mail/account`. Tests: `tests/test_mail_accounts.py`, `tests/mail-connect.test.js`.
- **Sign in to OAuth tools from Settings > Connections.** Catalog entries that use OAuth (most of the popular ones) no longer say "Set it up in Hermes": a Sign in button starts the flow in Hermes, opens the provider's page, watches until Hermes confirms and lists the tools. Atrium never handles the code or token, passes on only `https` sign-in addresses, and keeps entries that need a pre-registered app or local setup blocked. New routes `POST /connections/<name>/oauth/start`, `GET`/`DELETE /connections/oauth/flows/<id>`. Tests: `tests/test_connections.py`, `tests/connections-oauth.test.js`.
- **Skills and Memory can be edited from Settings.** A new Skills section lists the agent's skills (a `SKILL.md` each) and lets an owner read, add, change and remove them; the Memory section's notes can now be added, edited and removed too. Removing never erases: a note moves to a `.trash` folder beside it and a skill to `skills/.trash`. Writing works only where the folder is mounted writable (see `deploy/compose.example.yaml`); elsewhere the page is read-only and says so. Paths are checked (no `..`, no hidden folders, `.md` only), writes are atomic, and a skill must have a valid name and description header. New routes under `/api/plugins/atrium-memory` (`/status`, `POST` and `DELETE /note`) and `/api/plugins/atrium-skills`. Tests: `tests/test_memory_skills_edit.py`, `tests/skills-memory.test.js`.
- **Faster sign-in, Memory and background tabs; time zone data pinned.** `tzdata` is now pinned so calendar events with a `TZID` get their zone on any machine, including ones without a system time zone database (Windows development machines, minimal images). The deploy image already had one. Sign-in and role lookups and the Memory page no longer re-read their files on every request (they re-read only when the file changed), background tabs stop refreshing, and the repeated backend plumbing now lives in `app/agentkit.py`. See `docs/ARCHITECTURE-REVIEW.md`.
- **Email, Files and Calendar** (optional, owner only). Pages and routes appear only when `MAIL_*` or `NEXTCLOUD_*` settings exist (new page field `requires`).
  Mail connections are always verified (IMAP and SMTP implicit TLS); move and delete act on one message by UID; large messages are never truncated;
  sends are idempotent (`submission_id`) and report success once the server accepted the message even if saving to Sent fails. Uploads never replace
  an existing file unless asked. Synthetic tests: `tests/test_mail.py`, `tests/test_nextcloud.py`.
- **Agentic coding and the idea pipeline.** Sessions belong to projects, linked kanban cards show their worker runs read-only, and ideas belong to a
  project and are built inside it after a restore point (a failed restore point stops the build). Retrying an interrupted stage no longer creates a
  second card or project, and Approve everything records why it stopped. Sidecar: unique restore-point names, verified rewind, terminal access
  re-checked while open, and the agent's browser is reached through an authenticated entry point (`CDP_TOKEN`) instead of an open port.
  Tests: `tests/test_sidecar.py` plus additions to `tests/test_code.py` and `tests/test_pipeline.py`.
- **Your own chat model** (owners, Settings > Chat model): pick the install's default or a model from a connected cloud provider, per person, locked
  for that person's chats and Home ask box, with an explicit yes before chat text goes to a cloud provider. Also fixes the Home ask box, which
  treated Hermes' normal `202 Accepted` answer to a started run as a failure. Tests: `tests/test_chat_model.py`.
- **Color themes** (Settings > Appearance, everyone): seven dark palettes, saved per person and applied on first paint. Page stylesheets now use the
  design tokens instead of hard-coded brand colors (a mechanical, reversible swap; the default look is unchanged). Tests: `tests/test_themes.py`.
- **Sidebar**: a collapsible icon rail (remembered per browser), an optional accordion mode (`accordion_nav = true` in `instance.toml`), and a name and
  title on every entry. The sidebar tests (`nav.test.js`, `shim.test.js`) read each label's own element, which fixes the failures they had since the
  sidebar started drawing an icon tile beside each label. Tests: `tests/nav.test.js` (new cases), `tests/test_navigation.py`.
- **Home**: a one-line "what the agent is working on now", KPI tiles, SVG charts and gauges, a "Coming up" calendar card (only when a Nextcloud is
  configured), themed radar icons and trailing-link radar lines. A chat that spoke in the last 90 seconds now counts as work (scheduled check-ins never
  do). Tests: new cases in `tests/test_overview.py`.
- **Chat approvals**: one card per request showing the full command, no Approve button without a command, no buttons for requests that cannot be
  answered, one answer per click, and answers refused for a run that is no longer current (a stale card can no longer approve something in a later run).
  **Live read-aloud**: each sentence is spoken as soon as it has printed. Tests: `tests/chat-approval.test.js`, `tests/chat-live.test.js`,
  `tests/test_chat_assets.py`.
- **Avatar**: the Live agent character reacts to what happens (work, approval, done, failures with escalating frustration, welcome back); the icon of the
  tool the agent is really using glows on the 3D whiteboard and around the drawn avatar (`/api/tool-activity`, owners only, names and times only); installs
  can add their own looks with `[[avatar_presets]]` in `instance.toml` (validated). Tests: `tests/avatar.test.js`, `tests/test_tool_activity.py`.
- **Security fixes from the audit**: a sign-in ends when its password changes (also from the command-line tool), the sessions file is locked and safe against a
  damaged file; chat and run ownership fail closed; approval records are for owners only; the upload type check reads only the first bytes; the PDF, Word, Excel and
  PowerPoint readers are now installed by the requirements (they were imported but missing); the host updater no longer calls a failed package refresh "nothing waiting",
  keeps restore points until the previous version is verified, and can roll back a group that is down. Tests: `tests/test_audit_fixes.py` plus new cases in
  `tests/test_auth.py` and `tests/test_hostctl_daemon.py`.

## 1.0.1 (license change)

Atrium is now licensed under the **GNU Affero General Public License v3.0** (AGPL-3.0) instead of the MIT License.

- Version 1.0.0 and every commit up to `b5e180c` were published under the MIT License. Anyone who received those versions may keep using
  them under MIT. Everything after that commit is AGPL-3.0.
- No code changed in this release; only the license text and the places that mention it.

## 1.0.0

First public release of Atrium as a standalone web app (it grew out of a set of Hermes dashboard plugins).

- Sign-in with users and roles, sessions, lockout, per-person chats.
- Pages: Home, Chat, Workflows, Memory, Kanban, Agentic coding, Live agent, Room, Settings (with Persona and Avatar), Systems.
- Phone notifications, installable app, an optional approval gate for the agent's proposed actions.
- Instance folder for everything personal (brand, agent name, pages, theme, secrets); the framework is the same for everyone.
- `demo/run_demo.py`: the whole app on made-up sample data with a stand-in for Hermes, no agent needed.
