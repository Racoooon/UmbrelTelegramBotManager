# What to edit

The Telegram bot is **one file**: `thepiratebot.py`

Save it in this editor, then click **Restart** on the Status tab.

| File | What it is |
| --- | --- |
| `thepiratebot.py` | ThePirateBot itself (links, buttons, crypto, EN/DE text) |
| `defaults/config.json` | Factory settings used on first boot |
| `config.py` | How the dashboard reads/writes settings |
| `supervisor.py` | Start / stop / restart of the bot process |
| `main.py` | Starts the Umbrel dashboard |
| `web/server.py` | Dashboard API |
| `web/templates/index.html` | Dashboard layout |
| `web/static/app.js` | Dashboard buttons |
| `web/static/app.css` | Dashboard look |
| `requirements.txt` | Python packages |
| `entrypoint.sh` | Container startup |

`docker-compose.yml` and `umbrel-app.yml` live next to this `src/` folder in the store repo. Changing them here does not change how Umbrel launches the container until you update the GitHub store and reinstall / update the app.
