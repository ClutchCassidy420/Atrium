"""Container entry point: `uvicorn run:app`."""
import os

from main import make_app

os.umask(0o002)      # files this app creates are group-writable, so the agent (same group) can read and clean up shared files

app = make_app()
