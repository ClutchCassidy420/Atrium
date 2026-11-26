# Security

Atrium sits in front of an AI agent that can read files and take actions, so please treat a deployment as sensitive.

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's private reporting instead:
**Security tab > Report a vulnerability** on this repository. Include what you found, how to reproduce it, and which version.
You will get an answer as soon as I can manage; this is a small project run by one person.

## Running it safely

- Keep it on a private network (a VPN, or a reverse proxy that only your devices can reach). It is built for that, not for the open internet.
- Use long passwords. Passkeys and two-factor sign-in are not built in yet.
- Keep your Hermes keys in `config.private.env` and out of git. The `.gitignore` already skips that file.
- Give the app its own network to Hermes (see `deploy/compose.example.yaml`) and run it as a non-root user, as the example does.
- Only owners can approve the agent's proposed actions; keep the owner role for people you trust fully.

## Known limits

- The page policy still allows inline styles (`style-src 'unsafe-inline'`) because the bundled terminal library writes its own style rules. Scripts are never inline. Removing the allowance needs a nonce-based change to that library and is not done yet.
- Sign-in lockouts and rate limits are kept in memory, so they reset when the app restarts and are per process. Run one worker.
- The image includes a health check that calls `/healthz`; a reverse proxy or orchestrator can use the same address.

## Supported versions

Only the latest release gets fixes.
