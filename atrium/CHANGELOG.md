# Changelog

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
