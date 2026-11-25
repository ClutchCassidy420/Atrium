# Installing Atrium

The short version is in the [README](../README.md): `docker compose up -d --build`, read the setup code in the log, open
<http://localhost:8000>. This page covers everything after that.

## What the quick start gives you

- The app on **this computer only** (`127.0.0.1:8000`), signed in over plain HTTP, which is safe because nobody else can reach it.
- Your people, sessions and settings in a Docker volume (`atrium-data`), kept when you rebuild or update.
- A locked-down container: read-only files, no extra privileges, a memory limit.
- No agent connected yet, no email, files or calendar. Each of those is one setting.

## If something goes wrong

| Problem | What to do |
| --- | --- |
| I lost the setup code | `docker compose logs atrium`, or `docker compose exec atrium cat /data/setup-code.txt`. It exists only until the owner is made. |
| The page does not load | `docker compose ps` should show `atrium` running. `docker compose logs atrium` shows why not. Port 8000 busy? Put `ATRIUM_PORT=8080` in `.env` and run `docker compose up -d`. |
| I forgot my password | `docker compose exec atrium python /srv/scripts/manage_users.py /data passwd USERNAME` |
| Add another person | `docker compose exec atrium python /srv/scripts/manage_users.py /data add USERNAME user` (use `owner` for a second owner) |
| Start over | `docker compose down -v` deletes the volumes (every account and setting), then `docker compose up -d --build` starts fresh with a new setup code. |

## Connect your agent (Hermes)

1. Copy `.env.example` to `.env`.
2. Set `HERMES_API_BASE` and `HERMES_API_KEY` (and `HERMES_DASHBOARD_BASE` for the history and jobs pages). If Hermes runs on this
   same computer, write `host.docker.internal` where you would write `localhost`.
3. `docker compose up -d`.

To let the app see the agent's notes, jobs and uploads, point `compose.yaml` at its data folder: replace `atrium-agent:/opt/data` with
`/your/hermes/data:/opt/data`, and make that folder writable by the app's group (see `deploy/compose.example.yaml` for the exact
`chmod`). Skip this and everything that needs only the agent's API still works.

## Email, files and calendar, notifications

All optional, all set in `.env`. The full list with explanations is `config.private.example`: mailbox (`MAIL_*`), Nextcloud
(`NEXTCLOUD_*`), phone notifications (`PUSH_ENABLED=1`), text-to-speech (`TTS_URL`) and more.

## Reaching it from your phone or another computer

Do not just open the port. The quick start uses plain HTTP, so passwords would cross your network readable by anyone on it.

1. Put a reverse proxy with HTTPS in front of the container (Caddy, Nginx Proxy Manager or Traefik all work), or use a private
   network such as a VPN or Tailscale.
2. Remove `ALLOW_PLAIN_HTTP` from `compose.yaml` so sign-in cookies require HTTPS.
3. Tell the app which address is your proxy: `trusted_proxies` in the instance file (see [FRAMEWORK.md](FRAMEWORK.md)).

For a full server setup (private Docker networks, the agent's folders mounted read-only, a host helper for updates) start from
`deploy/compose.example.yaml` instead and read [FRAMEWORK.md](FRAMEWORK.md) first.

## Make it yours

Brand name, agent name and the sidebar are in the instance folder (`instance.example/instance.toml` is the template). Mount your
own copy by adding `- ./instance:/instance:ro` and `INSTANCE_DIR: /instance` to `compose.yaml`.

## Updating

```bash
git pull
docker compose up -d --build
```

Your accounts and settings stay in the volumes.

## Backing up

The only things worth keeping are the two volumes. For a quick copy of the app's own data:

```bash
docker run --rm -v atrium_atrium-data:/data -v "$PWD":/backup alpine tar czf /backup/atrium-data.tgz -C /data .
```

(The volume is named after the folder you cloned into: `docker volume ls` shows it.)
