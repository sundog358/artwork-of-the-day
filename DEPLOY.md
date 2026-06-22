# Deploying Artwork of the Day (public, OpenAI-backed)

This is the guide for a **live public site**. It uses the slim
[`Dockerfile.web`](Dockerfile.web) image (~150 MB, no GPU) and OpenAI for the AI
articles, so generations take a few seconds and scale with traffic.

> For an **offline / self-hosted** install with a bundled local model (Ollama),
> use the other image instead — see [DOCKER.md](DOCKER.md). That path needs a
> GPU box to be fast and is not recommended for a public site.

## What's already hardened for public traffic

- **Click-to-generate** — the page shows the free Wikidata summary by default and
  only calls OpenAI when the reader clicks "Write the full article". Bots and
  idle views cost nothing. (Set `AOTD_AUTO_GENERATE=1` to auto-write instead.)
- **Rate limiting** — the `?generate=1` endpoint is capped per IP
  (`10/hour; 3/min` by default); normal browsing gets a generous default.
- **Bounded caches** — in-process caches evict oldest-first at `AOTD_CACHE_MAX`
  (512) entries, so a crawler can't grow memory unbounded.
- **ProxyFix** — trusts the host's reverse proxy for the real client IP (correct
  rate-limit buckets) and the public https origin.

## Required environment

| Var | Value | Notes |
|-----|-------|-------|
| `AOTD_LLM_BACKEND` | `openai` | use the hosted API |
| `OPENAI_API_KEY`   | `sk-proj-…` | **secret** — set in the host's dashboard, never in git |
| `AOTD_ARTICLE_MODEL` | `gpt-4o-mini` | default; cheap + supports structured output |
| `AOTD_CONTACT` | your contact URL/email | sent in the Wikidata User-Agent (be polite) |

Optional tuning: `AOTD_AUTO_GENERATE`, `AOTD_RATELIMIT_DEFAULT`,
`AOTD_RATELIMIT_GENERATE`, `AOTD_RATELIMIT_STORAGE`, `AOTD_CACHE_MAX` — see
[.env.example](.env.example).

## Option A — Render (simplest)

A [`render.yaml`](render.yaml) blueprint is included.

1. Push this repo to GitHub.
2. Render → **New → Blueprint**, select the repo.
3. When prompted, paste your `OPENAI_API_KEY` (it's marked `sync: false`).
4. Deploy. Render gives you HTTPS, `$PORT`, and a health check automatically.

## Option B — Google Cloud Run (scale-to-zero, pay-per-request)

```sh
gcloud run deploy artwork-of-the-day \
  --source . \
  --port 5000 \
  --set-env-vars AOTD_LLM_BACKEND=openai,AOTD_ARTICLE_MODEL=gpt-4o-mini,AOTD_CONTACT=https://your/contact \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest \
  --allow-unauthenticated
```

Cloud Run builds from source; to force the slim image, build and push it first:
`docker build -f Dockerfile.web -t gcr.io/PROJECT/aotd . && docker push …`, then
`gcloud run deploy --image gcr.io/PROJECT/aotd`.

> Note: Cloud Run scales to multiple instances. The rate limiter and caches are
> per-instance (`memory://`). For strict global limits, point
> `AOTD_RATELIMIT_STORAGE` at a shared `redis://…`.

## Option C — Railway / Fly.io

Both detect the Dockerfile and inject `$PORT`. Point them at `Dockerfile.web`
(Railway: set the Dockerfile path; Fly: `fly launch --dockerfile Dockerfile.web`)
and set the same env vars as above.

## Local smoke test of the production image

```sh
docker build -f Dockerfile.web -t aotd-web .
docker run --rm -p 5000:5000 \
  -e AOTD_LLM_BACKEND=openai -e OPENAI_API_KEY=sk-... \
  -e AOTD_CONTACT=https://your/contact \
  aotd-web
# open http://localhost:5000, click "Write the full article"
```
