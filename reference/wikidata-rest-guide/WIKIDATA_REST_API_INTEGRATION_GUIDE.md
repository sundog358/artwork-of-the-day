# Wikidata REST API Integration Guide

Last updated: 2026-05-28

This document is a project-focused technical guide for integrating Wikidata's Wikibase REST API to maximum practical depth in `non-fiction-book-maker`.

It is designed to work with the current architecture:
- `book_maker.py` orchestration
- layered evidence harvesting (REST + SPARQL + MCP fallback)
- strict publication gates
- provenance and retrieval-quality reporting

---

## 1. Core Objectives

Use the REST API as:
1. Fast entity bootstrap (labels/descriptions/claims surface)
2. Deterministic statement/term retrieval for chapter evidence
3. Dynamic input generator for focused SPARQL templates
4. Stable, versioned, OpenAPI-based interface for long-term maintenance

Use SPARQL as:
1. Deep graph traversal
2. Multi-hop contextualization
3. Complex relationship extraction and aggregation

Use MCP as:
1. Assisted discovery/search
2. Schema-aware exploration
3. Fallback and iterative query support

---

## 2. Authoritative Base URLs

Wikidata REST API base:
- `https://www.wikidata.org/w/rest.php/wikibase/v1`

Key docs:
- Wikibase REST API docs (interactive): `https://doc.wikimedia.org/Wikibase/master/js/rest-api/`
- Wikidata REST API overview: `https://www.wikidata.org/wiki/Wikidata:REST_API`
- Action API vs REST comparison: `https://www.wikidata.org/wiki/Wikidata:REST_API/Comparison`

Notes:
- Use `v1` path for stable interface.
- Do not mix old `v0` paths in new code unless legacy compatibility is required.

---

## 3. Complete Swagger/OpenAPI Endpoint Inventory (v1.5, 33 paths)

Source of truth used for this section:
- `https://www.wikidata.org/w/rest.php/wikibase/v1/openapi.json`

Swagger mapping for your referenced route:
- `getPropertyLabelWithFallback` corresponds to:
  - `GET /v1/entities/properties/{property_id}/labels_with_language_fallback/{language_code}`

Full path list:

1. `POST /v1/entities/items`
2. `GET,PATCH /v1/entities/items/{item_id}`
3. `GET,PATCH /v1/entities/items/{item_id}/aliases`
4. `GET,POST /v1/entities/items/{item_id}/aliases/{language_code}`
5. `GET,PATCH /v1/entities/items/{item_id}/descriptions`
6. `GET,PUT,DELETE /v1/entities/items/{item_id}/descriptions/{language_code}`
7. `GET /v1/entities/items/{item_id}/descriptions_with_language_fallback/{language_code}`
8. `GET,PATCH /v1/entities/items/{item_id}/labels`
9. `GET,PUT,DELETE /v1/entities/items/{item_id}/labels/{language_code}`
10. `GET /v1/entities/items/{item_id}/labels_with_language_fallback/{language_code}`
11. `GET,PATCH /v1/entities/items/{item_id}/sitelinks`
12. `GET,PUT,DELETE /v1/entities/items/{item_id}/sitelinks/{site_id}`
13. `GET,POST /v1/entities/items/{item_id}/statements`
14. `GET,PUT,PATCH,DELETE /v1/entities/items/{item_id}/statements/{statement_id}`
15. `POST /v1/entities/properties`
16. `GET,PATCH /v1/entities/properties/{property_id}`
17. `GET,PATCH /v1/entities/properties/{property_id}/aliases`
18. `GET,POST /v1/entities/properties/{property_id}/aliases/{language_code}`
19. `GET,PATCH /v1/entities/properties/{property_id}/descriptions`
20. `GET,PUT,DELETE /v1/entities/properties/{property_id}/descriptions/{language_code}`
21. `GET /v1/entities/properties/{property_id}/descriptions_with_language_fallback/{language_code}`
22. `GET,PATCH /v1/entities/properties/{property_id}/labels`
23. `GET,PUT,DELETE /v1/entities/properties/{property_id}/labels/{language_code}`
24. `GET /v1/entities/properties/{property_id}/labels_with_language_fallback/{language_code}`
25. `GET,POST /v1/entities/properties/{property_id}/statements`
26. `GET,PUT,PATCH,DELETE /v1/entities/properties/{property_id}/statements/{statement_id}`
27. `GET /v1/openapi.json`
28. `GET /v1/property-data-types`
29. `GET /v1/search/items`
30. `GET /v1/search/properties`
31. `GET,PUT,PATCH,DELETE /v1/statements/{statement_id}`
32. `GET /v1/suggest/items`
33. `GET /v1/suggest/properties`

Coverage status in this file: `33/33` paths documented.

Verification method used (2026-05-28):
- fetched live OpenAPI JSON from `GET /v1/openapi.json`
- enumerated `paths` object
- cross-checked path count and method/path pairs against this list

OpenAPI snapshot metadata:
- `openapi`: `3.1.0`
- `info.title`: `Wikibase REST API`
- `info.version`: `1.5`

---

## 3.1 Critical Operation IDs (Swagger Naming)

Useful for codegen/tooling and direct traceability to Swagger panels:

1. `getPropertyLabelWithFallback`
- `GET /v1/entities/properties/{property_id}/labels_with_language_fallback/{language_code}`

2. `simpleItemSearch`
- `GET /v1/search/items`

3. `getSitelink`, `setSitelink`, `deleteSitelink`
- `GET|PUT|DELETE /v1/entities/items/{item_id}/sitelinks/{site_id}`

4. `getItemStatement`, `replaceItemStatement`, `patchItemStatement`, `deleteItemStatement`
- `GET|PUT|PATCH|DELETE /v1/entities/items/{item_id}/statements/{statement_id}`

5. Statement-global operations
- `replaceStatement`, `patchStatement`, `deleteStatement`
- `PUT|PATCH|DELETE /v1/statements/{statement_id}`

---

## 3.2 Canonical Shared Parameters and Headers (from OpenAPI components)

Parameter components (18):
- `Authorization` (header)
- `If-Match` (header)
- `If-None-Match` (header)
- `If-Modified-Since` (header)
- `If-Unmodified-Since` (header)
- `item_id` (path)
- `property_id` (path)
- `statement_id` (path; used in item/property/global statement routes)
- `language_code` (path)
- `site_id` (path)
- `_fields` (query; item/property field filtering)
- `property` (query filter)
- `language` (query; search/suggest language)
- `limit` (query)
- `offset` (query)

Header components returned by API:
- `ETag`
- `Last-Modified`
- `Content-Language`
- `X-Authenticated-User`
- `Location` (notably for created statement resources)

Practical rule:
- For robust clients, always support conditional request headers and read `ETag`/`Last-Modified` for cache coherence.

---

## 3.3 Write Content Types (exact OpenAPI behavior)

1. Standard write body:
- `application/json`

2. Patch-capable operations:
- accept both:
  - `application/json`
  - `application/json-patch+json`

Patch-capable families:
- item/property root patch
- labels patch
- descriptions patch
- aliases patch
- sitelinks patch
- statements patch (item/property/global)

Delete operations:
- use `application/json` request body (for comment/tags/bot metadata patterns).

---

## 3.4 Canonical Response Status Surface

Observed across OpenAPI paths:
- `200`, `201`, `304`, `307`, `308`, `400`, `403`, `404`, `409`, `412`, `422`, `429`, `500`

Important interpretation:
- `307` appears on language-fallback routes.
- `308` appears on canonical redirects (resource moved/canonicalized).
- `412` is heavily used for conditional-header precondition failures.
- `429` is common for write/rate-limited flows.

---

## 3.5 Core Schema Components (OpenAPI components/schemas)

Schemas exposed:
- `Item`
- `Property`
- `Statement`
- `Labels`
- `Descriptions`
- `Aliases`
- `Sitelink`

How to use in this project:
- Treat these as canonical shape references for adapter validation before conversion into evidence rows and RAG chunks.

---

## 4. "Full Potential" Hybrid Strategy

Implement layered lookups:

1. REST bootstrap (`what`)
- Pull core entity payload quickly
- Extract:
  - high-signal PIDs from statements/claims
  - related QIDs from values

2. SPARQL expansion (`how/why`)
- Build deterministic property-chain queries from extracted PIDs/QIDs
- Avoid unbounded patterns
- Use scoped `VALUES` and curated property subsets

3. Structured evidence block
- Normalize outputs into deterministic evidence rows:
  - `subject`, `predicate`, `object`, optional temporal context
  - source URL + metadata markers

4. RAG ingestion
- Chunk structured evidence + harvested source docs
- Preserve metadata:
  - rank
  - reference completeness
  - recency and retrieved-time markers

5. Generation and verification
- Feed "Core facts" and "Deep context" separately into prompts
- Maintain strict evidence alignment and fail-closed behavior

---

## 5. Current Project Integration Status

Already implemented in this repo:

1. REST v1 bootstrap constant and fetch helper
- `WIKIDATA_REST_ENTITY_TEMPLATE`
- `fetch_entity_rest_v1_snapshot(...)`

2. Dynamic signal extraction from REST payload
- `extract_property_and_qid_signals_from_rest_snapshot(...)`

3. Deterministic property-chain template library
- `build_property_chain_sparql_template_library(...)`
- Includes:
  - `entity_card`
  - `context_graph`
  - `timeline_events`

4. Timeout-safe SPARQL execution
- `run_sparql_with_timeout(...)`
- fallback to REST-only evidence on timeout/failure

5. Telemetry
- layered lookup diagnostics in chapter factcheck metrics:
  - rest bootstrap hits/misses
  - templates run
  - rows returned
  - REST-only fallback count

---

## 6. Recommended Request Policy

For all REST calls:

1. Headers
- `Accept: application/json`
- `User-Agent: non-fiction-book-maker/<version> (contact-url-or-email)`

2. Timeouts
- REST calls: 20-30s max
- SPARQL stage call wrapper: 15s max (already implemented)

3. Retry policy
- exponential backoff with jitter
- cap retries (2-4)
- classify retryable status codes (`429`, `5xx`)

4. Caching
- Separate namespaces for:
  - REST entity snapshots
  - search calls
  - SPARQL responses
- Long TTL for static-ish entity snapshots
- Short TTL for high-volatility endpoints if needed

5. Circuit behavior
- If SPARQL repeatedly fails, run REST-first evidence mode and continue generation with degraded depth

---

## 7. Data Modeling Rules for Quality

When transforming REST/SPARQL into narrative evidence:

1. Rank-aware selection
- prioritize `preferred`
- include `normal` for context
- suppress `deprecated` unless explicitly requested for historical conflict analysis

2. Temporal context
- include point/start/end/retrieved markers when available

3. Reference quality
- capture URL + stated-in entity + timestamps + metadata packet

4. Corroboration
- major factual claims require independent multi-source support per `major_claim_rules.json`

---

## 8. Operational Playbook (Per Chapter)

1. Discover seed entities and candidate properties
2. REST bootstrap for each seed QID
3. Build deterministic SPARQL templates from extracted signals
4. Execute SPARQL with timeout wrapper
5. Normalize rows into evidence facts
6. Harvest primary sources and metadata
7. Ingest into RAG with provenance metadata
8. Generate chapter with strict evidence alignment
9. Run citation verifier, provenance export, retrieval report, QA scorecard

---

## 9. Suggested Next Enhancements

1. OpenAPI-driven endpoint validator
- Fetch `/v1/openapi.json` at startup
- Verify required endpoints exist
- Feature-flag unavailable routes automatically

2. REST schema pinning artifact
- Save parsed OpenAPI version in run manifest

3. Endpoint capability matrix
- explicit map in code:
  - required for draft/research/publication profiles

4. REST field-shape adapters
- isolate shape changes by API version into adapter functions

5. Deterministic query templates catalog file
- externalize templates into JSON or `.sparql` files for auditable change control

---

## 10. Concrete Code Hooks

Primary integration points:

1. REST bootstrap and property/QID extraction
- `fetch_entity_rest_v1_snapshot(...)`
- `extract_property_and_qid_signals_from_rest_snapshot(...)`

2. Template generation and execution
- `build_property_chain_sparql_template_library(...)`
- `run_sparql_with_timeout(...)`
- `synthesize_property_chain_evidence_rows(...)`

3. Evidence flow
- `gather_wikidata_evidence_for_chapter(...)`

4. Reporting
- retrieval quality report
- run manifest / QA scorecard
- publication fail-closed audit artifacts

---

## 11. Testing Checklist

Unit tests should cover:

1. REST signal extraction
- statement PID extraction
- related QID extraction

2. Template library determinism
- fixed template names
- deterministic query construction for same inputs

3. SPARQL timeout fallback
- timeout path returns safe empty rows

4. Evidence synthesis
- temporal context formatting
- source URL mapping

5. Telemetry accounting
- layered lookup counters increment correctly

---

## 12. Compliance and Safety

1. Respect Wikimedia User-Agent policy and rate limits
2. Keep source attribution/provenance for every factual sentence
3. Maintain strict publication-mode fail-closed behavior when evidence quality checks fail
4. Treat REST/SPARQL data as input evidence, not final prose

---

## 13. References

1. Wikibase REST API interactive docs:
- https://doc.wikimedia.org/Wikibase/master/js/rest-api/

2. Wikidata REST API overview:
- https://www.wikidata.org/wiki/Wikidata:REST_API

3. REST vs Action API comparison:
- https://www.wikidata.org/wiki/Wikidata:REST_API/Comparison

4. Wikidata data access portal:
- https://www.wikidata.org/wiki/Wikidata:Data_access
