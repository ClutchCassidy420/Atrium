# Third-party notices

Atrium is licensed under the [MIT License](LICENSE). This page lists the code it bundles and the software it works with, so you
can see what you rely on and under which licences.

## Bundled in this repository

| Code | Where | Used for | Licence |
| --- | --- | --- | --- |
| [React](https://react.dev) and React DOM 18 | `app/static/vendor/` (licence in `REACT-LICENSE.txt`) | The page framework | MIT |
| [xterm.js](https://github.com/xtermjs/xterm.js) | `app/static/vendor/xterm/` (licence in `LICENSE` there) | The terminal in the coding workspace | MIT |
| [TalkingHead](https://github.com/met4citizen/TalkingHead) | built into `app/static/pages/live/engine.js` | Moving a 3D character's lips, face and body to speech | MIT |
| [Three.js](https://threejs.org) | built into `app/static/pages/live/engine.js` | Drawing the 3D character in the browser | MIT |

The licence texts for the two libraries inside `engine.js` are in `app/static/pages/live/engine.LICENSE.txt`. No 3D character,
animation clip, font or image from anyone else is included; the mascot and scenes are drawn in code.

## Python libraries (installed with `pip`, not bundled)

| Library | Used for | Licence |
| --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi) | The web routes | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | The web server | BSD-3-Clause |
| [HTTPX](https://github.com/encode/httpx) | Calls to Hermes and push services | BSD-3-Clause |
| [cryptography](https://github.com/pyca/cryptography) | Signing Web Push messages | Apache-2.0 or BSD-3-Clause |
| [websockets](https://github.com/python-websockets/websockets) | The terminal and browser streams | BSD-3-Clause |

## Services it can talk to (installed separately, not included)

| Service | What Atrium uses it for | Licence |
| --- | --- | --- |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | The agent itself, its API server and dashboard | MIT |
| A text-to-speech server such as [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) | Reading replies aloud (optional) | Apache-2.0 |
| [Anthropic API](https://www.anthropic.com/) | Optional cloud model for a chat or a Room turn | Service, under Anthropic's terms; you supply your own key |
| Web Push services run by browser makers | Delivering notifications to your phone or computer | Services, under their own terms |

## Trademarks

"Hermes Agent" and "Nous Research" are trademarks of their respective owners, as are the names of the other projects above. They
are used only to say what Atrium works with. Atrium is independent and not affiliated with or endorsed by any of them.

## If you add something

Anything you copy into this repository must come with its licence, and should be added to this page. Check that the licence allows
redistribution and keep the original copyright notice.
