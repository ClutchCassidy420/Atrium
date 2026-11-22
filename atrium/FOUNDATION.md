# Atrium source integration

The root is Hermes Agent at 3b421578588243f920886e6fe79a6faa87ea026e. The atrium directory imports the frontend at 9724b181a35f54ddcf322b61ce0aeb99430f0c2b, including the draft Connections PR atop d8fe4715ee8d07d3b0df3a30863c9e3e59cc521b. Both source commits are parents of the integration merge. Each project's license and notices remain alongside its source. Keep Vera-specific code in the downstream repository.

## Start the source build on Linux

Use a dedicated development account or disposable environment. Provision a runtime Python environment with the pinned root project's development setup and gateway, API server, dashboard and MCP dependencies. In a separate Python environment install atrium/requirements.txt. The frontend and runtime can have different dependency versions.

Set ATRIUM_RUNTIME_PYTHON and ATRIUM_WEB_PYTHON to their absolute interpreter paths. Set ATRIUM_STATE_DIR to an empty private directory owned by the developer. Supply distinct random API_SERVER_KEY and HERMES_DASHBOARD_SESSION_TOKEN secrets through the process environment. For a signed-in test, set OWNER_PASSWORD_HASH for the bootstrap owner in the example profile. Configure the test model in the runtime profile under ATRIUM_STATE_DIR/runtime. Leave production configuration outside the checkout and build from tracked sources only.

Run ./atrium/dev-stack.sh from the checkout. Open http://127.0.0.1:18648/login for the login page or /healthz for readiness of the frontend. The dashboard and gateway listen on loopback ports 18644 and 18645. Health of the frontend alone does not establish model or tool readiness. Use a verified HTTPS origin when testing a browser sign-in with secure cookies. Stop the launcher to stop its three child processes.

For release qualification, connect an approved HTTPS MCP fixture in Settings, test it, then ask a new chat to call a discovered tool. Verify the server received the argument and the chat returned its result after the gateway refreshed the tool registry.

## Updating the lineage

Fetch Hermes changes from the hermes remote and merge a reviewed commit into a candidate branch. For changes from the original frontend repository, fetch the atrium remote and merge with the atrium subtree prefix. Resolve conflicts and repeat the compatibility check before pinning a new release. The original source snapshots used here are shallow. Fetch older history from the corresponding remotes when a review requires it.
