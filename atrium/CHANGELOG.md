# Changelog

## Unreleased

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
