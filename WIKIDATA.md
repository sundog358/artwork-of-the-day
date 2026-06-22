# Wikidata Reference — Artwork of the Day

A focused reference for how this project talks to Wikidata. It covers the two
APIs in play, the queries and property IDs the app actually uses, the
performance lessons learned the hard way, and the image-URL handling.

> Scope note: this app is a **read-only consumer** of Wikidata. It never
> creates or edits entities. The Wikibase REST **write** endpoints are
> documented upstream but intentionally unused here.

---

## 1. Which API for what

| Need | Use | Endpoint |
| --- | --- | --- |
| "Find all paintings whose creator was born today" (filtering/searching the graph) | **SPARQL** | `https://query.wikidata.org/sparql` |
| "Give me everything about one entity (Q762)" | **Wikibase REST (read)** *or* a scoped SPARQL query | `https://www.wikidata.org/w/rest.php/wikibase/v1/...` |
| Editing Wikidata | Wikibase REST (write) — **out of scope** | — |

Rule of thumb: **SPARQL for discovery/filtering, REST for fetching one known
entity.** The current app uses SPARQL for both; the REST read API is a clean
alternative for the per-entity "fetch everything" step (handy for the planned
article-writer feature).

Always send a descriptive `User-Agent` (with contact info) on every request —
Wikimedia asks for it and may rate-limit anonymous/empty agents.

---

## 2. SPARQL

Endpoint: `GET https://query.wikidata.org/sparql` with params `query` and
`format=json`. Results land in `results.bindings` (a list of rows; each cell is
`{ "value": ... }`).

### 2.1 Item & property IDs used in this project

**Items (`wd:`)**

| ID | Meaning |
| --- | --- |
| `Q3305213` | painting *(used directly as `wdt:P31 wd:Q3305213`)* |
| `Q5` | human |
| `Q1028181` | painter (occupation) |
| `Q838948` | work of art *(the broad class — see the timeout warning below)* |

**Properties (`wdt:` for truthy values, `p:`/`psv:` for full statements)**

| ID | Meaning | Used for |
| --- | --- | --- |
| `P31` | instance of | identify paintings |
| `P279` | subclass of | (avoid the `P31/P279*` tree on paintings — slow) |
| `P170` | creator | artwork → artist |
| `P18` | image (Commons) | artwork & artist images |
| `P571` | inception | creation date |
| `P276` | location | current museum/collection |
| `P136` | genre | artwork genre |
| `P186` | material used | medium |
| `P2048` / `P2049` | height / width | dimensions |
| `P106` | occupation | filter to painters |
| `P569` / `P570` | date of birth / death | "born on this day", lifespan |
| `P19` / `P20` | place of birth / death | artist bio |
| `P27` | country of citizenship | nationality |
| `P135` | movement | art movement |
| `P800` | notable work | artist's famous works |
| `P737` | influenced by | (future: related-artist graph) |

### 2.2 Working query patterns

**Discovery — paintings by an artist born on a given month/day.** The app's
primary query. Note: **no label `SERVICE`** here (see perf notes), and start
from the small set of birthday-matching painters before joining to artworks.

```sparql
SELECT ?artwork ?image ?creator ?birth WHERE {
  ?creator wdt:P106 wd:Q1028181;     # occupation: painter
           wdt:P569 ?birth.          # date of birth
  FILTER(MONTH(?birth) = 6 && DAY(?birth) = 20)
  ?artwork wdt:P170 ?creator;        # created by this artist
           wdt:P31 wd:Q3305213;      # instance of: painting
           wdt:P18 ?image.           # must have an image
}
ORDER BY ?artwork
LIMIT 50
```

**Fallback — a random batch of paintings** (used only when a date has no
matching painter). Random offset into the full set keeps it varied; no label
`SERVICE`.

```sparql
SELECT ?artwork ?image ?creator WHERE {
  ?artwork wdt:P31 wd:Q3305213;
           wdt:P18 ?image;
           wdt:P170 ?creator.
}
LIMIT 50 OFFSET 8000   # offset randomized 0..~300000 in code
```

**Detail — one artwork** (scoped to a single entity, so the label `SERVICE` is
cheap here).

```sparql
SELECT ?artworkLabel ?date ?locationLabel ?genreLabel ?mediumLabel ?height ?width WHERE {
  BIND(wd:Q12418 AS ?artwork)
  OPTIONAL { ?artwork wdt:P571 ?date. }
  OPTIONAL { ?artwork wdt:P276 ?location. }
  OPTIONAL { ?artwork wdt:P136 ?genre. }
  OPTIONAL { ?artwork wdt:P186 ?medium. }
  OPTIONAL { ?artwork wdt:P2048 ?height. }
  OPTIONAL { ?artwork wdt:P2049 ?width. }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 1
```

**Detail — one artist, with multi-valued fields concatenated.** Aggregation
(`GROUP_CONCAT`) requires the **manual** form of the label service (binding each
label explicitly) rather than the automatic `[AUTO_LANGUAGE]` form. The
separator is `" || "` (not a comma) because labels themselves can contain
commas — splitting on `" || "` in code stays unambiguous.

```sparql
SELECT ?artistLabel ?artistDescription ?birth ?death ?birthPlaceLabel ?deathPlaceLabel ?image ?article
  (GROUP_CONCAT(DISTINCT ?nationalityLabel; separator=" || ") AS ?nationalities)
  (GROUP_CONCAT(DISTINCT ?movementLabel;    separator=" || ") AS ?movements)
  (GROUP_CONCAT(DISTINCT ?occupationLabel;  separator=" || ") AS ?occupations)
  (GROUP_CONCAT(DISTINCT ?notableWorkLabel; separator=" || ") AS ?notableWorks)
WHERE {
  BIND(wd:Q762 AS ?artist)
  OPTIONAL { ?artist wdt:P569 ?birth. }
  OPTIONAL { ?artist wdt:P570 ?death. }
  OPTIONAL { ?artist wdt:P19 ?birthPlace. }
  OPTIONAL { ?artist wdt:P20 ?deathPlace. }
  OPTIONAL { ?artist wdt:P18 ?image. }
  OPTIONAL { ?artist wdt:P27 ?nationality. }
  OPTIONAL { ?artist wdt:P135 ?movement. }
  OPTIONAL { ?artist wdt:P106 ?occupation. }
  OPTIONAL { ?artist wdt:P800 ?notableWork. }
  OPTIONAL { ?article schema:about ?artist; schema:isPartOf <https://en.wikipedia.org/>. }
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "en".
    ?artist       rdfs:label ?artistLabel; schema:description ?artistDescription.
    ?nationality  rdfs:label ?nationalityLabel.
    ?movement     rdfs:label ?movementLabel.
    ?occupation   rdfs:label ?occupationLabel.
    ?birthPlace   rdfs:label ?birthPlaceLabel.
    ?deathPlace   rdfs:label ?deathPlaceLabel.
    ?notableWork  rdfs:label ?notableWorkLabel.
  }
}
GROUP BY ?artistLabel ?artistDescription ?birth ?death ?birthPlaceLabel ?deathPlaceLabel ?image ?article
LIMIT 1
```

Useful extra: `?article schema:about ?artist; schema:isPartOf <https://en.wikipedia.org/>`
yields the English **Wikipedia URL** (a sitelink), outside the label service.

**Date precision** (if you ever filter by exact day on `P571`/inception):
truthy `wdt:P571` stores year-only dates as `YYYY-01-01`, so a naive month/day
filter floods January 1. Filter on statement precision instead:

```sparql
?artwork p:P571/psv:P571 ?ts.
?ts wikibase:timeValue ?date; wikibase:timePrecision ?prec.
FILTER(?prec >= 11)   # 11 = day precision (9 = year, 10 = month)
```

### 2.3 Performance lessons (measured against WDQS in this project)

These are the difference between "page loads in ~2s" and "504 timeout":

- **The `SERVICE wikibase:label` block is the dominant cost on large result
  sets.** The same batch query at offset 8000 took **~15s with** the label
  service and **~0.4s without** it. → Omit labels from discovery/batch queries;
  fetch labels per-entity in small scoped queries afterward.
- **Never use `wdt:P31/wdt:P279* wd:Q838948`** ("anything that is a work of art,
  transitively") joined against all painters. That was the original query and it
  **504-timed-out (~65s)** on Wikidata's own servers. Use the direct, indexed
  `wdt:P31 wd:Q3305213` (painting) instead.
- **Don't `ORDER BY RAND()`/hashing over the whole painting set** (~394k rows) —
  it 504-times-out. For randomness, use a **random `OFFSET`** instead (offset
  250000 still returned in ~8s; small offsets are sub-second).
- **Scoped single-entity queries are cheap** even with the label service
  (~1–2s), because they touch one item.
- Set an explicit **request timeout** in code (the app uses 30–45s) so a slow or
  down WDQS fails fast instead of hanging the request forever.
- This data is **deterministic per calendar day** → cache aggressively (ideally
  generate once/day). See the deployment notes in the project README/plan.

### 2.4 Images (Commons)

`P18` returns a URL like
`http://commons.wikimedia.org/wiki/Special:FilePath/<URL-encoded filename>`.
That URL already redirects to the actual file, so to get a sized thumbnail just:

1. switch `http://` → `https://`, and
2. append `?width=800` (or `&width=800` if a `?` is already present).

```
https://commons.wikimedia.org/wiki/Special:FilePath/Mona%20Lisa...jpg?width=800
→ HTTP 200, image/jpeg
```

> ⚠️ Do **not** hand-build `upload.wikimedia.org/.../thumb/<a>/<ab>/<file>/...`
> paths from the first letters of the filename. Those `<a>/<ab>` segments are the
> **MD5 hash** of the filename, not its first characters — an earlier version did
> this and every image 404'd. `Special:FilePath?width=` avoids the hash entirely.

### 2.5 Safety — SPARQL injection

Entity IDs get interpolated into queries as `wd:%s`. Any QID that originates
from a client request **must** be validated before use:

```python
QID_RE = re.compile(r'^Q\d+$')   # reject anything that isn't a bare Q-number
```

IDs that come straight from Wikidata results are already safe; the guard matters
on the public `/artwork-details` endpoint, which accepts `artwork`/`artist`
params from the browser.

---

## 3. Wikibase REST API (reads)

Base on Wikidata: `https://www.wikidata.org/w/rest.php/wikibase/v1`
(the upstream OpenAPI doc shows `https://wikibase.example/w/rest.php/wikibase`;
swap in `www.wikidata.org`). All read endpoints below were confirmed working
against live Wikidata.

Best for fetching **one known entity in full** without writing SPARQL — one
request returns labels, descriptions, aliases, statements, and sitelinks.

### 3.1 Endpoints

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/entities/items/{id}` | a whole item (filterable with `_fields`) |
| GET | `/entities/items/{id}/labels` | labels by language |
| GET | `/entities/items/{id}/descriptions` | descriptions by language |
| GET | `/entities/items/{id}/aliases` | aliases by language |
| GET | `/entities/items/{id}/statements` | all statements |
| GET | `/entities/items/{id}/sitelinks` | sitelinks (incl. Wikipedia) |
| GET | `/entities/properties/{id}` | a property definition |
| GET | `/statements/{statement_id}` | a single statement |

`{id}` is a QID (`Q762`) or PID (`P170`). Statement IDs look like
`Q762$2846ebc2-...`.

### 3.2 `_fields` filter

Trim the (large) item response to what you need:

```
GET /entities/items/Q762?_fields=labels,descriptions,statements,sitelinks
```
Allowed values: `type, labels, descriptions, aliases, statements, sitelinks`.

### 3.3 Conditional / caching headers

The API returns an `ETag` (entity revision) and `Last-Modified`. Use them to
avoid refetching unchanged data:

- `If-None-Match: "<etag>"` → `304 Not Modified` if unchanged.
- `If-Modified-Since: <http-date>` → same idea, by date.
- (`If-Match` / `If-Unmodified-Since` matter only for writes.)

Confirmed live: `etag: W/"2506687908"`, `last-modified: ... GMT`.

### 3.4 Entity / statement JSON shape

```jsonc
{
  "id": "Q762",
  "type": "item",
  "labels":       { "en": "Leonardo da Vinci", ... },
  "descriptions": { "en": "Italian Renaissance polymath (1452–1519)", ... },
  "aliases":      { "en": ["Leonardo", ...] },
  "sitelinks":    { "enwiki": { "title": "...", "url": "..." } },
  "statements": {
    "P170": [
      {
        "id": "Q762$<uuid>",
        "rank": "normal",                       // deprecated | normal | preferred
        "property": { "id": "P170", "data_type": "wikibase-item" },
        "value":    { "type": "value", "content": "Q762" },
        "qualifiers":  [ /* same property/value shape */ ],
        "references":  [ { "hash": "...", "parts": [ ... ] } ]
      }
    ]
  }
}
```

- `value.type` is `value`, `somevalue` (unknown), or `novalue` (none); `content`
  is present only for `value`.
- For item-valued statements, `content` is a QID string; resolve its label via a
  second call or a SPARQL label lookup.

### 3.5 Writes — out of scope

`POST`/`PUT`/`PATCH`/`DELETE` exist for creating and editing entities, labels,
statements, etc., and require `Authorization: Bearer <token>` (OAuth). This
project does not write to Wikidata, so they are intentionally not used here.

---

## 4. Licensing

- Wikidata **facts/structured data are CC0** — free to use without attribution.
- Wikipedia **article prose is CC BY-SA** — if the future article-writer ever
  ingests Wikipedia text (vs. generating prose from Wikidata facts), attribution
  and share-alike apply. Generating from CC0 facts keeps things simplest.
- Commons **images** carry their own per-file licenses; a public site should
  surface image credit/license where shown.

---

## 5. Article writer (deterministic — no model)

A short "About" article about each artwork, assembled **directly from the
Wikidata dossier** — no model, no API key, no per-token cost. Because every
sentence is composed from real Wikidata values, the result is trivially grounded:
it can never state anything the data doesn't support. The "make it read nicely"
step is plain Python string assembly, not generation.

**File**
- [article_writer.py](article_writer.py) — `build(artwork, artist)` → sectioned payload `{title, sections:[{heading, paragraphs:[…]}], mode, entities}`.
- Endpoint: `GET /artwork-article?artwork=Q…&artist=Q…` → `{status, article}`. Cached per artwork; reuses the `/artwork-details` dossier cache when warm.

**Sections** (each emitted only when it has supporting facts): *The painting*
(title, creator, date, place, medium, dimensions, genre, commission, series,
inventory) · *What it depicts* (depicted subjects + one-line glosses for named
ones) · *The artist* (bio, dates/places, training, teachers) · *Style and
influences* (movement, genres, influences with glosses, awards, memberships,
students) · *Where it lives today* (holding collection + its description) · *In
context* (contemporaries). Named entities are linked to Wikidata in the prose via
the dossier's `_link_entities` map. The frontend tags the result **📋 From
Wikidata**.

## 6. One-hop neighbourhood (Phase 2 — implemented)

Adds depth from Wikidata (no extra model cost). `gather_details()` in
[app.py](app.py) enriches each artwork/artist with a single extra SPARQL
round-trip via `get_neighborhood()`:

- Artwork: **depicts** (`P180`), **collection** (`P195`), **movement** (`P135`).
- Artist: **influenced by** (`P737`).

The query uses `VALUES (?subj ?rel ?p)` to fetch all relations for both entities
at once, returning plain `?entity ?entityLabel` rows that are paired into
`{qid, label}` in Python — deliberately **not** building `qid|label` pairs in
SPARQL, because `SERVICE wikibase:label` resolves last and a post-service `BIND`
silently yields nothing (learned the hard way). Entities with no English label
(label === QID) are dropped.

These feed two places:
- **The article gets deeper for free** — `depicts` and `influenced by` (with their
  short descriptions) feed the "What it depicts" and "Style and influences"
  sections, so the prose can say *who Lisa del Giocondo was*, not just name her.
- **Panel chips** — depicts / in-collection / influenced-by render as chips that
  link to `wikidata.org/wiki/<QID>` for the reader to follow.

## 7. SPARQL library & long-form articles (Phase 3 — implemented)

[sparql_library.py](sparql_library.py) is the data layer that assembles a rich
**dossier** per artwork+artist, feeding a multi-section **blog post**. All depth
comes from CC0 Wikidata — one extra dimension of data, not extra model calls.

`build_dossier(artwork_id, artist_id)` runs three efficient queries (~1s total):
- **`artwork_facts`** (scoped, single-valued): + inventory (`P217`), commissioned
  by (`P88`), series (`P179`), place of creation (`P1071`), country of origin
  (`P495`), main subject (`P921`) on top of the basics.
- **`artist_facts`** (scoped, **scalar only**): name, description, dates, places,
  image, Wikipedia. Multi-valued bio fields are NOT in this query.
- **`related`** (one `VALUES` query): all multi-valued fields as additive rows —
  depicts, collection, movement, influenced-by, nationality, occupation, notable
  works, education, teachers, students, genres, awards, memberships — each with a
  short description. Plus **`contemporaries`** (painters of the same movement
  born ±15 yrs).

> ⚠️ **The cartesian trap:** putting many multi-valued `OPTIONAL`s in one
> `GROUP_CONCAT` query explodes (a prolific artist like Leonardo → millions of
> rows → 45s timeout). The fix is here: scalars in a `LIMIT 1` query, multi-valued
> fields fetched as **additive rows** via `VALUES` and grouped in Python.

The dossier (~24 facts) drives [article_writer.py](article_writer.py)'s
deterministic multi-section "About" article (see §5). Each neighbourhood entry
carries a short Wikidata description, so the builder can render named entities as
`Label (description)` glosses — *Vincent van Gogh (Dutch painter)* — without any
model call. The panel also gains rows for teachers/students and chips for
contemporaries.

> The richer the dossier, the richer the prose — all of it CC0 Wikidata, one
> extra dimension of data rather than any model cost.

**Next phases** — Wikipedia links on chips; source references (`P854`) under the
article; per-artwork "angle" selection for variety.

## 8. Linked Art output ([linked.art](https://linked.art/api/1.0/))

We publish the data as **Linked Art** — a CIDOC-CRM profile in JSON-LD used
across cultural-heritage Linked Open Data. [linked_art.py](linked_art.py) maps
the dossier to records; [app.py](app.py) serves them. Five dereferenceable,
cross-linked record types (each `id` is its own retrieval URI; follow the graph):

- `GET /object/<QID>` → **HumanMadeObject**: `identified_by` (primary Name +
  accession Identifier), `classified_as` (AAT Painting/Artwork **+ genre**),
  `referred_to_by` (grounded description), `dimension` (cm + AAT units),
  `made_of` (medium → AAT, or Wikidata material URI), `produced_by` (creator →
  `/person`, `took_place_at` → `/place`, timespan), `current_location` →
  `/place`, `current_owner` → `/group`, `shows` → `/visual`, `representation`
  (image), `equivalent` (Wikidata).
- `GET /visual/<QID>` → **VisualItem**: `represents` the depicted subjects,
  each typed by CRM class (people → `Person`, places → `Place`, else `Type`,
  resolved from Wikidata `P31`/`P625`).
- `GET /person/<QID>` → **Person**: Name, `born`/`died` (TimeSpan +
  `took_place_at` → `/place`), description, `equivalent` (Wikidata).
- `GET /place/<QID>` → **Place**: Name, `defined_by` (WKT point from `P625`),
  `equivalent` (Wikidata + Getty **TGN**).
- `GET /group/<QID>` → **Group**: Name, `formed_by` (inception), `equivalent`
  (Wikidata + Getty **ULAN**).

**Protocol.** Records are served as
`application/ld+json;profile="https://linked.art/ns/v1/linked-art.json"`, with
**content negotiation** — `Accept: text/html` (or `?format=html`) returns a
human-readable HTML view at the same URI; `?format=jsonld` forces JSON-LD. Each
response carries the spec's **HAL `_links`** envelope (`self`, `curies`,
`la:modelVersion`, `la:apiVersion`). The page advertises the current artwork via
`<link rel="alternate" type="application/ld+json" href="/object/<QID>">`.

**Validation.** [validate_linked_art.py](validate_linked_art.py) checks the
emitted records against the **official Linked Art JSON Schemas** (vendored in
[linked_art_schema/](linked_art_schema/) from `linked-art/json-validator`,
Apache-2.0) — all five types pass. Run: `python validate_linked_art.py`
(needs `pip install -r requirements-dev.txt`). The HAL `_links` block is the
spec's *non-semantic* envelope, added at the response layer and excluded from
the schema-validated semantic body (the schemas set
`additionalProperties: false`).

**Remaining nuance:** depicted-subject typing uses a human/coordinate heuristic
(people and places are detected; other concepts default to the generic `Type`).
Everything else maps to AAT/TGN/ULAN where the authority exists, and to Wikidata
otherwise — so coverage is complete wherever the source data has the value.
