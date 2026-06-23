# Roadmap

> A living document. **Vision:** the richest, fully-*deterministic* art-history
> experience that can be built from open knowledge graphs — every claim a real
> fact or an attributed quote, no model in the loop.

Status keys: ✅ shipped · 🔨 in progress · 🔭 planned · 💡 idea.

## Guiding principles

These shape every item below — a feature that violates one isn't on the roadmap.

1. **Deterministic & grounded.** Every sentence traces to a Wikidata statement or
   attributed Wikipedia text. No generation, no hallucination surface.
2. **Right tool for each job.** Graph-shaped work on SPARQL; entity-by-id reads
   with qualifiers/references on the Wikibase REST API. Decisions are *measured*
   (see the git history), not assumed.
3. **Progressive & resilient.** The base article paints instantly; deep layers
   stream in, are cached, and **degrade gracefully** — a slow query drops one
   section, never the page.
4. **Cheap to run, free to read.** No API keys, no per-token cost, polite to the
   shared Wikimedia infrastructure (parallel but rate-capped).

---

## ✅ Shipped (v1)

The foundation is complete and live at **[metahistorybook.com](https://metahistorybook.com)**.

- ✅ Daily artwork tied to artists **born on today's date**, stable per day
- ✅ Instant deterministic base article ([article_writer.py](article_writer.py))
- ✅ **Generic, schema-agnostic Wikidata narrator** — property→template registry
  with a safe fallback ([wikidata_facts.py](wikidata_facts.py))
- ✅ **Multi-hop, notability-ranked enrichment** — academic lineage, peers,
  collections, depicted subjects, dated history, provenance ([enrichment.py](enrichment.py))
- ✅ **Hybrid two-API design** — SPARQL ([sparql_library.py](sparql_library.py)) +
  Wikibase REST for qualifier spans & references ([wikibase_rest.py](wikibase_rest.py))
- ✅ **Parallelized** enrichment (~3× faster); per-artwork caching
- ✅ **Explore** — click-through to any connected artist/work, date picker,
  "Surprise me", Back stack
- ✅ **Gallery (dark) mode**, accessibility (alt text, focus, ARIA), SEO
  (Open Graph + `schema.org/VisualArtwork`)
- ✅ **Linked Art** / CIDOC-CRM JSON-LD — 7 dereferenceable record types (object,
  visual, person, place, group, **concept, set**), authority links
  (VIAF/ULAN/TGN/ISNI/RKD/GeoNames), provenance & event activities, content
  negotiation, HAL, and CORS — all validated against the official JSON Schemas
  ([linked_art.py](linked_art.py))
- ✅ **IIIF Presentation 3.0** manifest per artwork + an in-app deep-zoom viewer
  ([iiif.py](iiif.py))
- ✅ Production hardening — rate limiting, bounded caches, ProxyFix, health check
- ✅ Tests + CI (ruff · format · mypy · pytest), licensing/attribution page

---

## 🔭 Near-term — the visual experience

The engine is rich in *text*; these add the *visual* and *sensory* dimensions.
Each is self-contained and screenshot-friendly.

### Look closely
- ✅ **Deep-zoom image viewer** — a IIIF Presentation 3.0 manifest per artwork
  ([iiif.py](iiif.py)) feeds an OpenSeadragon lightbox: click the painting to
  pan and zoom the full-resolution Commons image.
- 💡 **Colour palette** extracted from the painting; a dominant-colour backdrop
  behind the image.

### Place & time
- 🔭 **Map of the artist's world** — birthplace, death place, and where the work
  hangs today, from coordinates already available via `P625`.
- 🔭 **Visual life-and-work timeline** — born → trained → key works by year →
  died, rendering the chronology the article already computes.

### Read & listen
- 🔭 **Audio narration** of the article via the browser's speech-synthesis API —
  immersive *and* an accessibility win.
- 💡 **Glossary tooltips** — hover a genre/movement for its one-line definition
  (descriptions are already fetched).

---

## 🔭 Mid-term — depth & discovery

### Deeper enrichment
- 🔭 **Source footnotes** — surface `P854` reference URLs / `P248` sources as
  clickable citations under the article (extends the provenance line).
- 💡 **Iconography** — decode the Iconclass notation (`P1257`) Wikidata carries,
  the formal classification of *what is depicted*.
- 💡 **Qualifier-aware narration everywhere** — exhibitions with venues/years,
  positions held with start/end (the REST layer already exposes these).

### Discovery & navigation
- 🔭 **Browse by** movement / museum / genre (notability-ranked, like the daily list).
- 💡 **Search** for any artist or artwork (Wikidata `wbsearchentities`).
- 💡 **Favourites & recently-viewed** (localStorage), shareable.

### Reach
- 💡 **Multilingual** — labels and Wikipedia summaries in the reader's language
  (the SPARQL label service and REST API are language-parameterised already).

---

## 🔭 Engineering & quality

- ✅ **Route-level test coverage** — a Flask test-client suite exercising every
  route (assembly, headers, content negotiation, error paths) with the data layer
  mocked ([tests/test_routes.py](tests/test_routes.py)); 57 tests total.
- ✅ **Codebase-wide typing** — `mypy` now type-checks all 8 first-party modules
  (`app`, `sparql_library`, `enrichment`, `linked_art`, `iiif`, …), not just a few.
- 💡 **End-to-end enrichment test** — exercise the full parallel enrichment build
  against mocked HTTP, asserting graceful degradation when a query fails.
- 💡 **Multi-instance ready** — Redis-backed rate-limit storage and shared cache
  (the code already reads `AOTD_RATELIMIT_STORAGE`).
- 💡 **Observability** — structured logging and basic error tracking in place of
  `print`.
- 💡 **Edge caching** — a CDN in front, leaning on the once-a-day content cadence.

---

## 🚫 Non-goals

Deliberate scope decisions — as important as the roadmap itself.

- **No LLM / generative text.** The "📋 From Wikidata" badge is literally true and
  stays that way. If natural-language smoothing is ever revisited, it would be an
  *optional, fact-checked* layer behind a flag — never free-writing.
- **No write-back to Wikidata** — this is a read-only consumer of open data.
- **No personal accounts** — no login or per-user profiles; favourites stay
  client-side. Aggregate traffic analytics (Google Analytics 4) are used only to
  understand overall usage.
- **No scraping or un-attributed reuse** — every source is linked and licensed.

---

## How this is tracked

This file is the source of truth; significant items also become GitHub Issues as
they're picked up. Contributions that fit the **Guiding principles** are welcome —
the cleanest place to start is a single self-contained item under *Near-term*.
