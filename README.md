# Artwork of the Day

A small web app that shows a painting each day by an artist **born on today's
date**, pulled live from [Wikidata](https://www.wikidata.org). Flip through all
of the day's artworks with arrows; each shows the painting plus rich,
source-linked details about the artist and the work.

## Features

- **Tied to the date** — finds painters born on today's month/day and shows
  their paintings; the pick is stable for the whole day and rotates daily.
- **Gallery navigation** — arrows / keyboard (←/→) flip through every artwork
  found for the day.
- **Rich artist info** — description, birth/death (with places), nationality,
  movement, occupation, notable works, and a Wikipedia link.
- **Resilient & fast** — results are cached per day, so repeat visits are
  instant and the site keeps working even if Wikidata is briefly unavailable.
- **No-scroll layout** — artwork and info sit side-by-side and fit the viewport.

See [WIKIDATA.md](WIKIDATA.md) for how the Wikidata queries and data model work.

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

**Development** (auto-reload; set debug explicitly):

```bash
FLASK_DEBUG=1 python app.py        # http://127.0.0.1:5000
```

**Production** (Waitress WSGI server — works on Windows and Linux):

```bash
python serve.py                    # honors the PORT env var, default 5000
```

## Configuration

| Env var | Default | Purpose |
| --- | --- | --- |
| `PORT` | `5000` | Port to listen on |
| `FLASK_DEBUG` | `0` | `1`/`true` enables the dev server's debugger (never in prod) |
| `AOTD_CONTACT` | repo URL | Contact string sent in the Wikidata `User-Agent` |
| `AOTD_LLM_BACKEND` | `openai` if a key is set, else off | Which backend writes AI articles: `ollama` (free, local) or `openai` (paid). With neither, the app serves the deterministic Wikidata summary. |
| `AOTD_OLLAMA_MODEL` | `gemma4:latest` | Local Ollama model (needs Ollama running + the model pulled). |
| `AOTD_OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint. |
| `AOTD_OLLAMA_TIMEOUT` | `300` | Seconds to wait for local generation. |
| `OPENAI_API_KEY` (or `AOTD_OPENAI_API_KEY`) | _(unset)_ | Used when `AOTD_LLM_BACKEND=openai`. Read from `.env`. |
| `AOTD_ARTICLE_MODEL` | `gpt-4o-mini` | OpenAI model (when using the OpenAI backend). |

See [.env.example](.env.example) for a ready-to-copy config.

## Deploy

The app is a standard WSGI application (`app:app`) with a `Procfile`, so it runs
on most Python hosts (Render, Railway, Fly.io, a VPS, etc.). General steps:

1. Push this repo to the host (or a Git remote it builds from).
2. Build/install: `pip install -r requirements.txt`.
3. Start command: `python serve.py` (the included `Procfile` already does this).
4. Set `AOTD_CONTACT` to a real URL or email (Wikimedia asks for this).

Because the content changes only once a day, putting a CDN/cache in front of it
(or relying on the built-in daily cache + `Cache-Control: max-age=3600`) keeps
load on Wikidata minimal.

### Self-contained Docker (no API cost)

For a fully self-contained deployment — the web app, the Ollama server, and a
local LLM all baked into **one image** with no external API and no per-token
cost — see **[DOCKER.md](DOCKER.md)**:

```bash
docker build -t artwork-of-the-day .
docker run --rm -p 5000:5000 artwork-of-the-day      # add --gpus all if available
```

## Architecture

- `app.py` — Flask app. `/artwork-of-the-day` returns the day's gallery list;
  `/artwork-details?artwork=Q…&artist=Q…` returns details for one item. Both are
  cached (the gallery per day, details for the process lifetime).
- `serve.py` — production WSGI entry point (Waitress).
- `static/index.html` — the single-page frontend (vanilla JS).
- `WIKIDATA.md` — Wikidata/Wikibase query and data-model reference.

## Attribution

Data is from Wikidata (CC0). Images are from Wikimedia Commons under their
individual licenses — see each file on Commons.
