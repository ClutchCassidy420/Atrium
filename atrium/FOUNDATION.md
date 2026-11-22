# Atrium source integration

The root is Hermes Agent at 3b421578588243f920886e6fe79a6faa87ea026e. The atrium directory imports the frontend at 9724b181a35f54ddcf322b61ce0aeb99430f0c2b, including the draft Connections PR atop d8fe4715ee8d07d3b0df3a30863c9e3e59cc521b. Both source commits are parents of the integration merge. Each project's license and notices remain alongside its source. Keep Vera-specific code in the downstream repository.

## Start the source build on Linux

From this checkout on Linux, run the following as your own user. The prerequisites are Bash 5.1+, uv, util-linux flock, and GNU coreutils. The first run requires network access for dependencies.

```bash
./atrium/start
```

Choose a password of at least 12 characters for the initial **owner** account at the hidden prompt. The launcher provisions Python 3.14 environments for the gateway and web app, generates private service keys, and starts the three processes. A subsequent run reuses the account, service keys and runtime state. Use Ctrl-C to stop the stack.

Open http://localhost:18648/login on the same machine. The dashboard and gateway listen on loopback ports 18644 and 18645. For HTTPS, set ATRIUM_TLS_CERT and ATRIUM_TLS_KEY to an appropriate trusted certificate chain and its PEM private key before starting. Open the HTTPS URL matching that certificate. Session cookies retain their Secure flag.

By default the profile lives under `$XDG_DATA_HOME/atrium` or `~/.local/share/atrium`. Override it with ATRIUM_STATE_DIR for a disposable install. Keep the directory private (0700) and outside the checkout. Sign in as owner and open **Settings → Chat model**. Enter the OpenAI-compatible API base URL, exact model ID and optional key, then select **Use model**. Start a fresh chat to use the saved model. Remote endpoints require HTTPS. A local model on the same host can use a loopback HTTP URL.

For development with existing Python environments, atrium/dev-stack.sh accepts ATRIUM_RUNTIME_PYTHON, ATRIUM_WEB_PYTHON, ATRIUM_STATE_DIR, API_SERVER_KEY and HERMES_DASHBOARD_SESSION_TOKEN.

For release qualification, connect an approved HTTPS MCP fixture in Settings, test it, then ask a new chat to call a discovered tool. Verify the server received the argument and the chat returned its result after the gateway refreshed the tool registry.

## Updating the lineage

Fetch Hermes changes from the hermes remote and merge a reviewed commit into a candidate branch. For changes from the original frontend repository, fetch the atrium remote and merge with the atrium subtree prefix. Resolve conflicts and repeat the compatibility check before pinning a new release. The original source snapshots used here are shallow. Fetch older history from the corresponding remotes when a review requires it.

## Upgrade and rollback on Linux

Use the checkout that actually runs the current release to record its revision. The checkpoint command requires a stopped profile and a clean checkout. For a release that predates this command, run the newer checkpoint utility with `--release "$current_checkout"` when saving. Checkpoints contain private keys and account data. Use trusted private storage outside Git. Symlinks retain their targets. Back up external dependencies separately, including a custom INSTANCE_DIR, provider environment, external databases and TLS files.

1. Fetch the reviewed candidate revision. Create a separate checkout with `git worktree add --detach "$candidate_checkout" "$candidate_sha"` and exercise it with a disposable profile. Stop the trial before restarting any other local stack on the same ports.
2. Stop the installed stack. From the **current release checkout**, run `./atrium/checkpoint save "$profile" "$checkpoint"`. The destination must be new and its parent must exist. Keep the current checkout and checkpoint through the rollback window.
3. Start the candidate with `ATRIUM_STATE_DIR="$profile" "$candidate_checkout/atrium/start"` and the install's usual environment. Verify HTTPS owner login, the selected model and a tool call in a fresh chat.
4. To roll back, stop the candidate. Run `./atrium/checkpoint restore "$checkpoint" "$recovered_profile"` with a new private profile path. Start the original checkout at the revision in release.json with ATRIUM_STATE_DIR pointing to the recovered profile. Restore any external dependencies and confirm referenced paths still resolve. New writes made after the checkpoint remain in the candidate's profile. Keep it for reconciliation.

The launcher provisions its virtual environments on first use of a restored profile. Keep the pinned source and dependency cache available if rollback needs to work offline.
