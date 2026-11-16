# Host helper (server actions)

`atrium-hostctl.py` is a tiny program that runs on the **host** (as root, under systemd) and does four fixed things for the web app:
install the "safe" package updates, install the "care" updates (Docker and friends), update or roll back one **container group**, reboot, and shut down. It powers **Settings >
Server**. Without it, that section simply does not appear.

Why a separate helper: the web container never gets root or the Docker socket. It sends a request; the helper checks three things
before it acts: the caller's address, a shared key, and, for every change, that the person's own session cookie is accepted by the
web app's `/api/gate-check` route (owners only). Reboot and shutdown wait out a countdown (30 seconds by default) that can be cancelled.
Updates run as their own systemd job, so they survive the web app being restarted by the update. Everything is logged to
`/var/lib/atrium-hostctl/log.jsonl`.

## Install

1. Copy `atrium-hostctl.py` to `/usr/local/sbin/` and `atrium-hostctl.service` to `/etc/systemd/system/`.
2. Copy `hostctl.env.example` to `/etc/atrium/hostctl.env` (mode 600, root), set a long random `HOSTCTL_KEY`, the web container's address, and the
   address the helper listens on (an address only the web container can reach, for example the Docker bridge address).
3. Allow only the web container to reach that port in your firewall.
4. Put `HOST_ACTION_URL` (for example `http://172.20.0.1:8766`) and the same key as `HOST_ACTION_KEY` in the app's `config.private.env`, then restart the app.
5. `systemctl enable --now atrium-hostctl`.

Set `HOSTCTL_DRYRUN=1` first to try it: updates are simulated and reboot and shutdown do nothing.

`HOSTCTL_CARE` lists package-name prefixes that can restart services (they get their own button with a warning). `HOSTCTL_BACKUP_UNITS`
names systemd units that mean "a backup is running", which the page warns about.

A shutdown leaves the machine off until someone presses its power button. Nothing here can start it again.

## Container updates and rollback

The helper finds every Docker Compose group that has a running container (no configuration needed) and, every few hours, asks the image
registries whether something newer exists:

- **Floating tags** (`latest` and the like): compares the registry's digest with the one you run. One cheap header-only request per image.
- **Version tags** (`4.10.2.0`, `v0.23.1`): lists the registry's tags and offers the highest newer one of the **same shape and the same major
  version** (never the next major version, which can change data in ways a rollback cannot undo; for 0.x versions it stays inside the minor version).
- Images fixed by digest and images built on the server are reported as such and never touched.

Groups are sorted into **tiers** by name: `HOSTCTL_TIER_LOW` and `HOSTCTL_TIER_MEDIUM` list the groups the page may update. Everything else is
**high risk** and is only reported, never updated by the helper.

An update works on one group at a time:
1. It keeps the current image under its own tag (`atrium-restore/...`) and copies the compose file (a restore point, kept `HOSTCTL_KEEP_DAYS` days).
2. For a version tag it changes the version in the compose file; then it pulls and recreates only that group's changed services.
3. It waits until every container is running, healthy (if it has a health check) and not restarting, for a stable stretch.
4. If that fails, it **rolls back by itself**: the compose file and the old image come back and the containers are recreated.

**Roll back** does the same on request, as long as the restore point exists. A rollback puts the program back, not its data: if a newer version
changed its data files, restore a backup too. Registries limit anonymous requests (Docker Hub allows about 100 per six hours per address), so a
check that hits the limit says "couldn't check right now" and never claims "up to date"; running `docker login` on the host raises the limit.
