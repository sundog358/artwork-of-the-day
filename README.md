# Artwork of the Day

[![CI](https://github.com/sundog358/artwork-of-the-day/actions/workflows/ci.yml/badge.svg)](https://github.com/sundog358/artwork-of-the-day/actions/workflows/ci.yml)
[![Live](https://img.shields.io/badge/live-metahistorybook.com-1f5b86)](https://metahistorybook.com)
[![Code: MIT](https://img.shields.io/badge/code-MIT-green)](LICENSE)
[![Data: CC0 / CC BY-SA](https://img.shields.io/badge/data-CC0%20%2F%20CC%20BY--SA-blue)](https://metahistorybook.com/legal)

A daily art-history explorer. Every day it shows a painting by an artist **born
on today's date**, then builds a rich, **deterministic, fully-cited article**
about it — and the people, places, movements and institutions around it — live
from open knowledge graphs. **No LLM, no API keys, no per-token cost.** Every
sentence is a real Wikidata fact or attributed Wikipedia text.

**▶ Live: [metahistorybook.com](https://metahistorybook.com)**

<!-- Add two screenshots to docs/ to complete the showcase:
     docs/screenshot.png (light) and docs/gallery.png (dark gallery mode). -->
<!--
![Artwork of the Day](docs/screenshot.png)
![Gallery mode](docs/gallery.png)
-->

---

## Why it's interesting

This isn't a CRUD app — it's a small **knowledge-graph narration engine**. The
hard parts:

- A **schema-agnostic narrator** that turns *any* Wikidata entity's statements
  into prose from a property→template registry (add a fact type = one line).
- **Multi-hop graph traversal**, notability-ranked — *"In Gérôme's circle of
  pupils, Paxton trained alongside Mary Cassatt, Thomas Eakins…"* — every name
  clickable to fall down the rabbit hole.
- A deliberate **two-API hybrid**: SPARQL for everything graph-shaped (search,
  traversal, ranking), and the **Wikibase REST API** for the entity-by-id reads
  where it genuinely wins (qualifier spans, references) — taking load off the
  rate-limited SPARQL endpoint.
- **Parallelized I/O** — ~16 independent SPARQL/REST/Wikipedia calls run
  concurrently, so a deeply-enriched article assembles in ~2–3s.

## Features

- **Tied to the date** — painters born on today's month/day; stable per day.
- **Deep, deterministic articles** — the work's facts, a closer look at the
  depicted subjects, the artist's life, genre & tradition, academic lineage &
  peers, where the work is held, dated history (*"stolen 1911, recovered 1913"*),
  the work's place in the artist's life, related works, and data provenance.
- **Explore** — click any related work / artist / movement to load it; a date
  picker for any day; "🎲 Surprise me"; a Back stack.
- **Gallery (dark) mode** — paintings on a museum wall, persisted across visits.
- **Linked Art API** — every artwork is also a dereferenceable
  [Linked Art](https://linked.art) / CIDOC-CRM JSON-LD record (`/object/<QID>`).
- **Production-ready** — rate limiting, bounded caches, reverse-proxy awareness,
  a health check, and live, per-item attribution.

## Architecture

```mermaid
flowchart LR
    U[Browser - vanilla JS SPA] -->|/artwork-of-the-day, /resolve, /surprise| A[Flask app.py]
    U -->|/artwork-article, /artwork-enrichment| A
    U -->|/object /person /place ... | LA[linked_art.py - JSON-LD]
    A --> SL[sparql_library.py]
    A --> EN[enrichment.py - parallel orchestrator]
    EN --> SL
    EN --> WF[wikidata_facts.py - generic narrator]
    EN --> WR[wikibase_rest.py]
    EN --> WP[Wikipedia REST summaries]
    SL -->|graph queries, traversal, ranking| WDQS[(Wikidata SPARQL / WDQS)]
    WF -->|batched statements| WDQS
    WR -->|qualifiers and references, off-WDQS| REST[(Wikibase REST API)]
    A --> CACHE[in-process bounded caches]
```

The base article is instant; the **progressive enrichment** streams in below it
(and is cached per artwork), so first paint never waits on the deep layers.

## Key technical decisions

These are the trade-offs I'd talk through in a review:

- **Deterministic over generative.** The app once had an optional LLM layer; I
  removed it. Every line is now a real fact or attributed quote, the "📋 From
  Wikidata" badge is literally true, and there are no keys/cost. Trust over
  fluency.
- **Right API for each job (measured, not assumed).** I prototyped moving the
  label-heavy entity reads to the Wikibase REST API and **measured 40 value-label
  lookups per entity** (REST has no batch-label endpoint) vs **1** SPARQL query
  with labels inline — so I kept those on SPARQL and used REST only for qualifier
  spans and references, where it's actually better. The reasoning is in the git
  history.
- **Knowing when to stop.** A "paintings from year *N*" query was un-indexable on
  WDQS (~23s scanning 390k rows); I dropped it and used same-collection /
  same-movement instead. Silent truncation and slow features are worse than fewer.
- **Parallelism with politeness.** Enrichment fans out across a thread pool capped
  at 5 workers (WDQS's per-IP concurrency), cutting wall-time ~3× with graceful
  degradation — a throttled query drops one section, never the page.

See **[WIKIDATA.md](WIKIDATA.md)** for the query/data-model deep-dive.

## Tech stack

Python · Flask · Waitress · Flask-Limiter · SPARQL (WDQS) · Wikibase REST API ·
Wikimedia REST · Linked Art (CIDOC-CRM JSON-LD) · vanilla JS (no build step) ·
Docker · Render · GitHub Actions · pytest.

## Run locally

Requires Python 3.10+.

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows  (source .venv/bin/activate on *nix)
pip install -r requirements.txt -r requirements-dev.txt

python serve.py                   # production WSGI (Waitress) → http://127.0.0.1:5000
# FLASK_DEBUG=1 python app.py     # dev server with auto-reload
```

It needs **no secrets**. Optional env vars (`AOTD_CONTACT`, rate limits, cache
size) are in [.env.example](.env.example).

## Tests

```bash
pytest -q
```

A fast, no-network unit suite over the deterministic core — the property
narrator, qualifier/date parsing, entity-card phrasing, and SPARQL value
helpers (the logic the article's trustworthiness depends on). The same checks
run in CI on every push.

## Deploy

Standard WSGI app, no secrets. A [`render.yaml`](render.yaml) blueprint and a
slim [`Dockerfile.web`](Dockerfile.web) are included — see
[DEPLOY.md](DEPLOY.md) for one-click Render / Cloud Run / Railway.

## Project structure

| File | Role |
| --- | --- |
| `app.py` | Flask routes, caching, rate limiting, ProxyFix, health check |
| `sparql_library.py` | SPARQL: dossier, traversal, notability ranking, aggregation |
| `wikidata_facts.py` | Generic schema-agnostic statement → sentence narrator |
| `wikibase_rest.py` | Wikibase REST client: qualifier spans + references (off-WDQS) |
| `enrichment.py` | Parallel orchestrator that assembles the progressive article |
| `article_writer.py` | The instant base "About" article from the dossier |
| `linked_art.py` | Linked Art / CIDOC-CRM JSON-LD records |
| `static/index.html` | Single-page frontend (vanilla JS) |
| `tests/` | pytest unit suite |

## Licensing & attribution

- **Code** — MIT ([LICENSE](LICENSE)).
- **Facts** — [Wikidata](https://www.wikidata.org), CC0 1.0.
- **Article text** — [Wikipedia](https://en.wikipedia.org), CC BY-SA 4.0,
  attributed inline.
- **Images** — [Wikimedia Commons](https://commons.wikimedia.org), per-file
  licenses; each image links to its Commons file page.

Full, user-facing breakdown at **[`/legal`](https://metahistorybook.com/legal)**.
Attribution is maintained **live** — each item links back to its sources.
