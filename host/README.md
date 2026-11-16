# Host helper (server actions)

`atrium-hostctl.py` is a tiny program that runs on the **host** (as root, under systemd) and does four fixed things for the web app:
install the "safe" package updates, install the "care" updates (Docker and friends), reboot, and shut down. It powers **Settings >
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
