# Deploying Artwork of the Day

This is the guide for a **live public site**. It uses the slim
[`Dockerfile.web`](Dockerfile.web) image (~150 MB, no GPU). The app is **pure
Wikidata/SPARQL** — no model, no API key, no per-token cost — so there are **no
secrets** to configure.

## What's already hardened for public traffic

- **Rate limiting** — the Wikidata-hitting endpoints are capped per IP
  (`1200/hour; 120/min` by default); the home page (`/`) and health check
  (`/healthz`) are exempt.
- **Bounded caches** — in-process caches evict oldest-first at `AOTD_CACHE_MAX`
  (512) entries, so a crawler can't grow memory unbounded.
- **ProxyFix** — trusts the host's reverse proxy for the real client IP (correct
  rate-limit buckets) and the public https origin.

## Environment

Everything is optional. The one worth setting on a deploy:

| Var | Value | Notes |
|-----|-------|-------|
| `AOTD_CONTACT` | your contact URL/email | sent in the Wikidata User-Agent (be polite) |

Optional tuning: `AOTD_RATELIMIT_DEFAULT`, `AOTD_RATELIMIT_STORAGE`,
`AOTD_CACHE_MAX` — see [.env.example](.env.example).

## Option A — Render (simplest)

A [`render.yaml`](render.yaml) blueprint is included.

1. Push this repo to GitHub.
2. Render → **New → Blueprint**, select the repo.
3. Deploy. Render gives you HTTPS, `$PORT`, and a health check automatically.
   No secrets to enter.

## Option B — Google Cloud Run (scale-to-zero, pay-per-request)

```sh
gcloud run deploy artwork-of-the-day \
  --source . \
  --port 5000 \
  --set-env-vars AOTD_CONTACT=https://your/contact \
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
and set `AOTD_CONTACT`.

## Local smoke test of the production image

```sh
docker build -f Dockerfile.web -t aotd-web .
docker run --rm -p 5000:5000 -e AOTD_CONTACT=https://your/contact aotd-web
# open http://localhost:5000
```
