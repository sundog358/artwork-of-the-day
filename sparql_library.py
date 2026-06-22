"""SPARQL library — assembles a rich Wikidata "dossier" for one artwork + artist.

This is the data layer for the long-form (blog-post) article writer. It gathers
many dimensions of context in a handful of efficient queries, so the article has
real substance to draw on — all from CC0 Wikidata, no model cost:

  - artwork facts (date, medium, genre, dimensions, location, inventory,
    commissioned by, series, place of creation, country of origin, main subject)
  - artist biography (dates/places, nationality, movements, occupations, notable
    works, education, teachers, students, genres, awards, memberships)
  - one-hop entity neighbourhoods WITH short descriptions (what the painting
    depicts; who influenced the artist) so the prose can explain each thing
  - contemporaries (other painters of the same movement, born around the same
    time)

`build_dossier(artwork_id, artist_id)` returns `(artwork, artist)` dicts that are
a superset of what the panel already expects, plus the richer fields. Lessons
baked in: query labels via the label SERVICE in the SELECT (never build qid|label
pairs in a post-SERVICE BIND), and skip entities whose label is just their QID.
"""
import re

import requests

WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
import os

_CONTACT = os.environ.get("AOTD_CONTACT", "https://github.com/jchirum/artwork-of-the-day")
HEADERS = {"User-Agent": f"ArtworkOfTheDay/1.0 ({_CONTACT})"}

QID_RE = re.compile(r"^Q\d+$")


def run_sparql(query, timeout=45):
    """Execute a SPARQL query against Wikidata and return the result bindings."""
    response = requests.get(
        WDQS_ENDPOINT,
        params={"format": "json", "query": query},
        headers=HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get("results", {}).get("bindings", [])


def qid(uri):
    """Extract the bare Q-id from a Wikidata entity URI."""
    return uri.rsplit("/", 1)[-1] if uri else ""


def commons_thumb(image_url, width=800):
    """Turn a Wikidata P18 (commons FilePath) URL into an https thumbnail URL."""
    if not image_url:
        return ""
    image_url = image_url.replace("http://", "https://")
    sep = "&" if "?" in image_url else "?"
    return f"{image_url}{sep}width={width}"


def format_date(date_str):
    """Format an ISO date string into a human-readable date.

    Wikidata stores year-only precision as ``YYYY-01-01``; render that as just the
    year rather than a misleading "January 01" (genuine 1 January dates are rare
    and indistinguishable in this representation)."""
    if not date_str:
        return "Unknown"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.month == 1 and dt.day == 1:
            return str(dt.year)
        return dt.strftime("%B %d, %Y")
    except Exception:
        return date_str


def _v(row, key):
    return row.get(key, {}).get("value", "")


def _qid_of(row, key):
    """Extract a bare Q-id from a row's entity-URI binding (or '')."""
    q = qid(_v(row, key))
    return q if QID_RE.match(q) else ""


def _labels_from(items, limit):
    """Join the labels of a [{qid,label}] list into a capped phrase."""
    labels = [d["label"] for d in (items or []) if d.get("label")]
    text = ", ".join(labels[:limit])
    return text + ("…" if len(labels) > limit else "")


# --------------------------------------------------------------------------- #
# Artwork facts (single scoped query)                                         #
# --------------------------------------------------------------------------- #
def artwork_facts(artwork_id):
    query = """
    SELECT ?artworkLabel ?date ?genre ?genreLabel ?medium ?mediumLabel ?height ?width
           ?location ?locationLabel ?inventory ?commissionedByLabel ?seriesLabel
           ?creationPlace ?creationPlaceLabel ?country ?countryLabel
           ?mainSubjectLabel ?image WHERE {
      BIND(wd:%s AS ?artwork)
      OPTIONAL { ?artwork wdt:P571 ?date. }            # inception
      OPTIONAL { ?artwork wdt:P136 ?genre. }           # genre
      OPTIONAL { ?artwork wdt:P186 ?medium. }          # material/medium
      OPTIONAL { ?artwork wdt:P2048 ?height. }
      OPTIONAL { ?artwork wdt:P2049 ?width. }
      OPTIONAL { ?artwork wdt:P276 ?location. }        # current location
      OPTIONAL { ?artwork wdt:P217 ?inventory. }       # inventory number
      OPTIONAL { ?artwork wdt:P88 ?commissionedBy. }   # commissioned by
      OPTIONAL { ?artwork wdt:P179 ?series. }          # part of the series
      OPTIONAL { ?artwork wdt:P1071 ?creationPlace. }  # location of creation
      OPTIONAL { ?artwork wdt:P495 ?country. }         # country of origin
      OPTIONAL { ?artwork wdt:P921 ?mainSubject. }     # main subject
      OPTIONAL { ?artwork wdt:P18 ?image. }            # image (Commons)
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 1
    """ % artwork_id

    results = run_sparql(query, timeout=30)
    if not results:
        return {}
    r = results[0]

    title = _v(r, "artworkLabel")
    if not title or QID_RE.match(title):
        title = "Untitled"

    dimensions = ""
    h, w = _v(r, "height"), _v(r, "width")
    if h and w:
        dimensions = f"{h}cm × {w}cm"

    return {
        "title": title,
        "creationDate": format_date(_v(r, "date")),
        "creationDateRaw": _v(r, "date"),   # ISO, for Linked Art timespans
        "genre": _v(r, "genreLabel") or "Unknown",
        "genreQid": _qid_of(r, "genre"),
        "medium": _v(r, "mediumLabel") or "Unknown",
        "mediumQid": _qid_of(r, "medium"),
        "dimensions": dimensions or "Unknown",
        "heightCm": _v(r, "height"),        # raw numeric strings, for Linked Art dimensions
        "widthCm": _v(r, "width"),
        "location": _v(r, "locationLabel") or "Unknown",
        "locationQid": _qid_of(r, "location"),
        "inventory": _v(r, "inventory"),
        "commissionedBy": _v(r, "commissionedByLabel"),
        "series": _v(r, "seriesLabel"),
        "creationPlace": _v(r, "creationPlaceLabel"),
        "creationPlaceQid": _qid_of(r, "creationPlace"),
        "country": _v(r, "countryLabel"),
        "countryQid": _qid_of(r, "country"),
        "mainSubject": _v(r, "mainSubjectLabel"),
        "image": commons_thumb(_v(r, "image")),
    }


# --------------------------------------------------------------------------- #
# Artist biography (single scoped query, multi-valued fields concatenated)     #
# --------------------------------------------------------------------------- #
def artist_facts(artist_id):
    """Scalar (single-valued) artist fields only — fast. Multi-valued bio fields
    are fetched as additive rows in build_dossier() to avoid GROUP_CONCAT
    cartesian blow-up on prolific artists."""
    query = """
    SELECT ?artistLabel ?artistDescription ?birth ?death ?birthPlace ?birthPlaceLabel
           ?deathPlace ?deathPlaceLabel ?image ?article WHERE {
      BIND(wd:%s AS ?artist)
      OPTIONAL { ?artist wdt:P569 ?birth. }
      OPTIONAL { ?artist wdt:P570 ?death. }
      OPTIONAL { ?artist wdt:P19 ?birthPlace. }
      OPTIONAL { ?artist wdt:P20 ?deathPlace. }
      OPTIONAL { ?artist wdt:P18 ?image. }
      OPTIONAL { ?article schema:about ?artist; schema:isPartOf <https://en.wikipedia.org/>. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 1
    """ % artist_id

    results = run_sparql(query, timeout=30)
    if not results:
        return {}
    r = results[0]

    name = _v(r, "artistLabel")
    if not name or QID_RE.match(name):
        name = "Unknown artist"
    birth_raw = _v(r, "birth")
    birth_year = int(birth_raw[:4]) if (len(birth_raw) >= 4 and birth_raw[:4].isdigit()) else None
    death = _v(r, "death")

    return {
        "name": name,
        "description": _v(r, "artistDescription"),
        "birthdate": format_date(birth_raw),
        "birthDateRaw": birth_raw,          # ISO, for Linked Art Birth timespan
        "birthplace": _v(r, "birthPlaceLabel"),
        "birthPlaceQid": _qid_of(r, "birthPlace"),
        "deathdate": format_date(death) if death else "",
        "deathDateRaw": death,
        "deathplace": _v(r, "deathPlaceLabel"),
        "deathPlaceQid": _qid_of(r, "deathPlace"),
        "image": commons_thumb(_v(r, "image")),
        "wikipedia": _v(r, "article"),
        "_birthYear": birth_year,
    }


def creator_of(artwork_qid):
    """Return the QID of an artwork's creator (P170), or '' if none."""
    if not QID_RE.match(artwork_qid):
        return ""
    query = "SELECT ?c WHERE { wd:%s wdt:P170 ?c. } LIMIT 1" % artwork_qid
    try:
        rows = run_sparql(query, timeout=20)
    except Exception as e:
        print(f"creator_of error: {e}")
        return ""
    return qid(rows[0]["c"]["value"]) if rows else ""


# --------------------------------------------------------------------------- #
# Facts for dereferenceable Place / Group records (Linked Art)                #
# --------------------------------------------------------------------------- #
def place_facts(place_qid):
    """Scalar facts for a place: label, description, WKT point, Getty TGN id."""
    if not QID_RE.match(place_qid):
        return {}
    query = """
    SELECT ?placeLabel ?placeDescription ?coord ?tgn ?typeLabel WHERE {
      BIND(wd:%s AS ?place)
      OPTIONAL { ?place wdt:P625 ?coord. }     # coordinate location
      OPTIONAL { ?place wdt:P1667 ?tgn. }      # Getty TGN ID
      OPTIONAL { ?place wdt:P31 ?type. }       # instance of (e.g. city)
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 1
    """ % place_qid
    try:
        rows = run_sparql(query, timeout=20)
    except Exception as e:
        print(f"place_facts error: {e}")
        return {}
    if not rows:
        return {}
    r = rows[0]
    name = _v(r, "placeLabel")
    if not name or QID_RE.match(name):
        name = "Unknown place"
    # P625 comes back as a WKT literal "Point(lon lat)"; normalise to "POINT(...)".
    coord = _v(r, "coord")
    wkt = coord.replace("Point(", "POINT(").replace("point(", "POINT(") if coord else ""
    return {
        "name": name,
        "description": _v(r, "placeDescription"),
        "wkt": wkt,
        "tgn": _v(r, "tgn"),
        "type": _v(r, "typeLabel"),
    }


def group_facts(group_qid):
    """Scalar facts for a group/institution: label, description, inception, TGN/ULAN."""
    if not QID_RE.match(group_qid):
        return {}
    query = """
    SELECT ?groupLabel ?groupDescription ?inception ?ulan WHERE {
      BIND(wd:%s AS ?group)
      OPTIONAL { ?group wdt:P571 ?inception. }   # inception (date formed)
      OPTIONAL { ?group wdt:P245 ?ulan. }        # Getty ULAN ID
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 1
    """ % group_qid
    try:
        rows = run_sparql(query, timeout=20)
    except Exception as e:
        print(f"group_facts error: {e}")
        return {}
    if not rows:
        return {}
    r = rows[0]
    name = _v(r, "groupLabel")
    if not name or QID_RE.match(name):
        name = "Unknown group"
    return {
        "name": name,
        "description": _v(r, "groupDescription"),
        "inception": _v(r, "inception"),
        "ulan": _v(r, "ulan"),
    }


def enrich_entities(qids):
    """Second-hop enrichment: one batched 'tell me about these' query.

    For the named entities an article will mention (depicted people, the art
    movement, the holding museum, influences, peers), fetch a compact fact —
    a one-line description plus life dates — so the prose can say *who/what*
    each thing is, not just name it. One round-trip, no model cost. Birth/death
    are SAMPLEd to a single value to avoid row blow-up; the label service
    auto-fills ?eLabel/?eDescription (entities lacking a description stay in).

    Returns {qid: {"label", "description", "birth", "death"}}.
    """
    valid = [q for q in dict.fromkeys(qids) if QID_RE.match(q)]
    out = {}
    if not valid:
        return out
    values = " ".join(f"wd:{q}" for q in valid)
    query = """
    SELECT ?e ?eLabel ?eDescription (SAMPLE(?birth) AS ?b) (SAMPLE(?death) AS ?d) WHERE {
      VALUES ?e { %s }
      OPTIONAL { ?e wdt:P569 ?birth. }
      OPTIONAL { ?e wdt:P570 ?death. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    GROUP BY ?e ?eLabel ?eDescription
    """ % values
    try:
        rows = run_sparql(query, timeout=30)
    except Exception as e:
        print(f"enrich_entities error: {e}")
        return out
    for r in rows:
        q = qid(_v(r, "e"))
        label = _v(r, "eLabel")
        if not q or (label and QID_RE.match(label)):
            continue
        out[q] = {
            "label": label,
            "description": _v(r, "eDescription"),
            "birth": _v(r, "b"),
            "death": _v(r, "d"),
        }
    return out


def classify_entities(qids):
    """Map each QID to a CIDOC-CRM class for Linked Art references.

    Used to type the subjects a painting `shows`. We detect the two cases that
    matter for depicted subjects — a human (`P31 = Q5` → Person) and a place
    (anything carrying a coordinate `P625` → Place) — and fall back to the
    generic `Type` for concepts/things. One batched query, no per-entity calls.
    """
    valid = [q for q in dict.fromkeys(qids) if QID_RE.match(q)]
    out = {q: "Type" for q in valid}
    if not valid:
        return out
    values = " ".join(f"wd:{q}" for q in valid)
    query = """
    SELECT ?e (SAMPLE(?human) AS ?isHuman) (SAMPLE(?coord) AS ?hasCoord) WHERE {
      VALUES ?e { %s }
      OPTIONAL { ?e wdt:P31 ?cls. BIND(IF(?cls = wd:Q5, 1, 0) AS ?human) }
      OPTIONAL { ?e wdt:P625 ?coord. }
    }
    GROUP BY ?e
    """ % values
    try:
        rows = run_sparql(query, timeout=25)
    except Exception as e:
        print(f"classify_entities error: {e}")
        return out
    for r in rows:
        q = qid(_v(r, "e"))
        if _v(r, "isHuman") == "1":
            out[q] = "Person"
        elif _v(r, "hasCoord"):
            out[q] = "Place"
    return out


# --------------------------------------------------------------------------- #
# One-hop neighbourhood with descriptions                                     #
# --------------------------------------------------------------------------- #
def related(specs):
    """specs: list of (subject_qid, key, pid). Returns {key: [{qid,label,description}]}."""
    out = {key: [] for _, key, _ in specs}
    valid = [(s, k, p) for (s, k, p) in specs if QID_RE.match(s)]
    if not valid:
        return out
    values = " ".join(f'(wd:{s} "{k}" wdt:{p})' for s, k, p in valid)
    query = """
    SELECT ?rel ?entity ?entityLabel ?entityDescription WHERE {
      VALUES (?subj ?rel ?p) { %s }
      ?subj ?p ?entity.
      SERVICE wikibase:label {
        bd:serviceParam wikibase:language "en".
        ?entity rdfs:label ?entityLabel; schema:description ?entityDescription.
      }
    }
    LIMIT 500
    """ % values
    try:
        results = run_sparql(query, timeout=30)
    except Exception as e:
        print(f"Error fetching related entities: {e}")
        return out
    seen = set()
    for r in results:
        rel = _v(r, "rel")
        ent = qid(_v(r, "entity"))
        label = _v(r, "entityLabel")
        if rel in out and ent and label and not QID_RE.match(label) and (rel, ent) not in seen:
            seen.add((rel, ent))
            out[rel].append({"qid": ent, "label": label, "description": _v(r, "entityDescription")})
    return out


# --------------------------------------------------------------------------- #
# Contemporaries (best-effort)                                                 #
# --------------------------------------------------------------------------- #
def contemporaries(movement_qids, birth_year, exclude_qid, span=15, limit=6):
    if not movement_qids or not birth_year:
        return []
    values = " ".join(f"wd:{q}" for q in movement_qids if QID_RE.match(q))
    if not values:
        return []
    query = """
    SELECT DISTINCT ?artist ?artistLabel ?artistDescription WHERE {
      VALUES ?movement { %s }
      ?artist wdt:P106 wd:Q1028181 ;
              wdt:P135 ?movement ;
              wdt:P569 ?b .
      FILTER(YEAR(?b) >= %d && YEAR(?b) <= %d && ?artist != wd:%s)
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT %d
    """ % (values, birth_year - span, birth_year + span, exclude_qid, limit)
    try:
        results = run_sparql(query, timeout=30)
    except Exception as e:
        print(f"Error fetching contemporaries: {e}")
        return []
    out, seen = [], set()
    for r in results:
        ent = qid(_v(r, "artist"))
        label = _v(r, "artistLabel")
        if ent and label and not QID_RE.match(label) and ent not in seen:
            seen.add(ent)
            out.append({"qid": ent, "label": label, "description": _v(r, "artistDescription")})
    return out


# --------------------------------------------------------------------------- #
# Enrichment layers (progressive — fetched after the base article renders)     #
# --------------------------------------------------------------------------- #
def wikipedia_sitelink(qid, lang="en"):
    """Return the Wikipedia article title for an entity (or '' if none)."""
    if not QID_RE.match(qid):
        return ""
    query = """
    SELECT ?article WHERE {
      ?article schema:about wd:%s;
               schema:isPartOf <https://%s.wikipedia.org/>.
    }
    LIMIT 1
    """ % (qid, lang)
    try:
        rows = run_sparql(query, timeout=20)
    except Exception as e:
        print(f"wikipedia_sitelink error: {e}")
        return ""
    if not rows:
        return ""
    url = _v(rows[0], "article")
    return url.rsplit("/wiki/", 1)[-1] if "/wiki/" in url else ""


def artist_works(artist_id, exclude_qid="", limit=8):
    """Other paintings by this artist (P170), most-documented first. Returns
    [{qid, label, year}] — for an 'other works by X' line with links."""
    if not QID_RE.match(artist_id):
        return []
    # Require an image (P18) so every listed work is openable in the explore view.
    query = """
    SELECT ?w ?wLabel (SAMPLE(?d) AS ?date) WHERE {
      ?w wdt:P170 wd:%s; wdt:P31 wd:Q3305213; wdt:P18 ?img.
      OPTIONAL { ?w wdt:P571 ?d. }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    GROUP BY ?w ?wLabel
    LIMIT 40
    """ % artist_id
    try:
        rows = run_sparql(query, timeout=30)
    except Exception as e:
        print(f"artist_works error: {e}")
        return []
    out, seen = [], set()
    for r in rows:
        q = qid(_v(r, "w"))
        label = _v(r, "wLabel")
        if not q or q == exclude_qid or q in seen or not label or QID_RE.match(label):
            continue
        seen.add(q)
        year = _v(r, "date")[:4]
        out.append({"qid": q, "label": label, "year": year if year.isdigit() else ""})
        if len(out) >= limit:
            break
    return out


def artwork_context(artwork_id):
    """Extra, often-sparse artwork context: exhibition history (P608), the events
    it commemorates/depicts (P793), and its art movement (P135). One VALUES query;
    returns {exhibitions, events, movements} as label lists."""
    out = {"exhibitions": [], "events": [], "movements": []}
    if not QID_RE.match(artwork_id):
        return out
    query = """
    SELECT ?rel ?vLabel WHERE {
      VALUES (?rel ?p) { ("exhibitions" wdt:P608) ("events" wdt:P793) ("movements" wdt:P135) }
      wd:%s ?p ?v.
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 100
    """ % artwork_id
    try:
        rows = run_sparql(query, timeout=25)
    except Exception as e:
        print(f"artwork_context error: {e}")
        return out
    for r in rows:
        rel, label = _v(r, "rel"), _v(r, "vLabel")
        if rel in out and label and not QID_RE.match(label) and label not in out[rel]:
            out[rel].append(label)
    return out


def artist_collections(artist_id, limit=10):
    """Distinct museums/collections that hold this artist's paintings (P195
    across their P170 works), most-held first. Returns [{label, n}] — institutional
    context ('held at the Met, the MFA…') and the comparative 'most-collected at X'."""
    if not QID_RE.match(artist_id):
        return []
    query = """
    SELECT ?cLabel (COUNT(DISTINCT ?w) AS ?n) WHERE {
      ?w wdt:P170 wd:%s; wdt:P31 wd:Q3305213; wdt:P195 ?c.
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    GROUP BY ?cLabel
    ORDER BY DESC(?n)
    LIMIT 25
    """ % artist_id
    try:
        rows = run_sparql(query, timeout=30)
    except Exception as e:
        print(f"artist_collections error: {e}")
        return []
    out, seen = [], set()
    for r in rows:
        label = _v(r, "cLabel")
        if label and not QID_RE.match(label) and label not in seen:
            seen.add(label)
            try:
                n = int(_v(r, "n"))
            except ValueError:
                n = 0
            out.append({"label": label, "n": n})
        if len(out) >= limit:
            break
    return out


def artist_identifiers(artist_id):
    """Authority/research identifiers for outbound links: VIAF (P214), Getty ULAN
    (P245), RKD (P650), Library of Congress (P244), Commons category (P373)."""
    out = {}
    if not QID_RE.match(artist_id):
        return out
    query = """
    SELECT ?viaf ?ulan ?rkd ?loc ?commons WHERE {
      OPTIONAL { wd:%s wdt:P214 ?viaf. }
      OPTIONAL { wd:%s wdt:P245 ?ulan. }
      OPTIONAL { wd:%s wdt:P650 ?rkd. }
      OPTIONAL { wd:%s wdt:P244 ?loc. }
      OPTIONAL { wd:%s wdt:P373 ?commons. }
    }
    LIMIT 1
    """ % ((artist_id,) * 5)
    try:
        rows = run_sparql(query, timeout=25)
    except Exception as e:
        print(f"artist_identifiers error: {e}")
        return out
    if not rows:
        return out
    for k in ("viaf", "ulan", "rkd", "loc", "commons"):
        v = _v(rows[0], k)
        if v:
            out[k] = v
    return out


def artist_traditions(artist_id):
    """The traditions an artist belongs to: genres (P136), movements (P135),
    schools/academies they trained at (P69), and teachers (P1066, 'student of') —
    each with a Wikidata description, and the seeds for the hop-2 lineage layers.
    Returns {genres, movements, education, teachers} as [{qid, label, description}]."""
    out = {"genres": [], "movements": [], "education": [], "teachers": [], "nationality": []}
    if not QID_RE.match(artist_id):
        return out
    query = """
    SELECT ?rel ?v ?vLabel ?vDescription WHERE {
      VALUES (?rel ?p) { ("genres" wdt:P136) ("movements" wdt:P135)
                         ("education" wdt:P69) ("teachers" wdt:P1066)
                         ("nationality" wdt:P27) }
      wd:%s ?p ?v.
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 60
    """ % artist_id
    try:
        rows = run_sparql(query, timeout=25)
    except Exception as e:
        print(f"artist_traditions error: {e}")
        return out
    seen = set()
    for r in rows:
        rel, q, label = _v(r, "rel"), qid(_v(r, "v")), _v(r, "vLabel")
        if rel in out and q and label and not QID_RE.match(label) and (rel, q) not in seen:
            seen.add((rel, q))
            out[rel].append({"qid": q, "label": label, "description": _v(r, "vDescription")})
    return out


def artist_relations(artist_id):
    """People around the artist: spouse (P26), students (P802), and artists who
    cite them as an influence (reverse P737). Returns {spouse, students,
    influenced} label lists."""
    out = {"spouse": [], "students": [], "influenced": []}
    if not QID_RE.match(artist_id):
        return out
    query = """
    SELECT ?rel ?vLabel WHERE {
      { wd:%s wdt:P26 ?v. BIND("spouse" AS ?rel) }
      UNION { wd:%s wdt:P802 ?v. BIND("students" AS ?rel) }
      UNION { ?v wdt:P737 wd:%s. BIND("influenced" AS ?rel) }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    LIMIT 60
    """ % ((artist_id,) * 3)
    try:
        rows = run_sparql(query, timeout=25)
    except Exception as e:
        print(f"artist_relations error: {e}")
        return out
    for r in rows:
        rel, label = _v(r, "rel"), _v(r, "vLabel")
        if rel in out and label and not QID_RE.match(label) and label not in out[rel]:
            out[rel].append(label)
    return out


# --------------------------------------------------------------------------- #
# Enrichment engine: generic entity-expander (one batched "tell me about these")#
# --------------------------------------------------------------------------- #
def expand_entities(qids):
    """The reusable building block: given any set of QIDs, return a normalised
    'card' for each — label, description, a type (instance-of), life/inception
    dates, and country/place — in ONE batched query. Layers narrate these cards
    instead of each writing its own query.

    Returns {qid: {label, description, type, birth, death, inception, country,
    admin}}. Multi-valued props are SAMPLEd to one value to avoid row blow-up.
    """
    valid = [q for q in dict.fromkeys(qids) if QID_RE.match(q)]
    out = {}
    if not valid:
        return out
    values = " ".join(f"wd:{q}" for q in valid)
    query = """
    SELECT ?e ?eLabel ?eDescription (SAMPLE(?type) AS ?t) (SAMPLE(?b) AS ?birth)
           (SAMPLE(?d) AS ?death) (SAMPLE(?inc) AS ?inception)
           (SAMPLE(?ctry) AS ?country) (SAMPLE(?adm) AS ?admin) WHERE {
      VALUES ?e { %s }
      OPTIONAL { ?e wdt:P31 ?typeItem. ?typeItem rdfs:label ?type. FILTER(LANG(?type) = "en") }
      OPTIONAL { ?e wdt:P569 ?b. }
      OPTIONAL { ?e wdt:P570 ?d. }
      OPTIONAL { ?e wdt:P571 ?inc. }
      OPTIONAL { ?e wdt:P17 ?ctryItem. ?ctryItem rdfs:label ?ctry. FILTER(LANG(?ctry) = "en") }
      OPTIONAL { ?e wdt:P131 ?admItem. ?admItem rdfs:label ?adm. FILTER(LANG(?adm) = "en") }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    GROUP BY ?e ?eLabel ?eDescription
    """ % values
    try:
        rows = run_sparql(query, timeout=35)
    except Exception as e:
        print(f"expand_entities error: {e}")
        return out
    for r in rows:
        q = qid(_v(r, "e"))
        label = _v(r, "eLabel")
        if not q or (label and QID_RE.match(label)):
            continue
        out[q] = {
            "label": label,
            "description": _v(r, "eDescription"),
            "type": _v(r, "t"),
            "birth": _v(r, "birth"),
            "death": _v(r, "death"),
            "inception": _v(r, "inception"),
            "country": _v(r, "country"),
            "admin": _v(r, "admin"),
        }
    return out


def notable_by_property(seed_qids, pid, exclude="", limit=6, painter_only=False, min_links=4):
    """Hop-2 traversal: entities that point to a seed via `pid` (e.g. students of
    a teacher, alumni of a school, members of a movement, painters in a genre),
    ranked by Wikidata sitelink count as a notability proxy so we surface the
    famous names, not the long tail. Returns [{qid, label}].

    Pattern is reverse: `?x wdt:PID ?seed`. min_links prunes obscure entities up
    front so ORDER BY over a big set stays fast."""
    valid = [q for q in dict.fromkeys(seed_qids) if QID_RE.match(q)]
    if not valid:
        return []
    values = " ".join(f"wd:{q}" for q in valid)
    painter = "?x wdt:P106 wd:Q1028181." if painter_only else ""
    excl = f"FILTER(?x != wd:{exclude})" if QID_RE.match(exclude) else ""
    query = """
    SELECT ?x ?xLabel (MAX(?sl) AS ?links) WHERE {
      VALUES ?seed { %s }
      ?x wdt:%s ?seed.
      %s
      ?x wikibase:sitelinks ?sl.
      FILTER(?sl >= %d)
      %s
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    GROUP BY ?x ?xLabel
    ORDER BY DESC(?links)
    LIMIT %d
    """ % (values, pid, painter, min_links, excl, limit * 2)
    try:
        rows = run_sparql(query, timeout=40)
    except Exception as e:
        print(f"notable_by_property({pid}) error: {e}")
        return []
    out, seen = [], set()
    for r in rows:
        q, label = qid(_v(r, "x")), _v(r, "xLabel")
        if q and label and not QID_RE.match(label) and q not in seen:
            seen.add(q)
            out.append({"qid": q, "label": label})
        if len(out) >= limit:
            break
    return out


def genre_peers(genre_qids, nationality_qid, exclude="", limit=6, min_links=4):
    """Notable painters who share the artwork's genre AND the artist's nationality,
    ranked by notability — 'American portraitists like Sargent, Whistler…'. The
    nationality constraint keeps it relevant (not just the most famous painters of
    all time) and the result set small enough to rank quickly. Returns [{qid,label}]."""
    genres = [q for q in dict.fromkeys(genre_qids) if QID_RE.match(q)]
    if not genres or not QID_RE.match(nationality_qid):
        return []
    values = " ".join(f"wd:{q}" for q in genres)
    excl = f"FILTER(?x != wd:{exclude})" if QID_RE.match(exclude) else ""
    query = """
    SELECT ?x ?xLabel (MAX(?sl) AS ?links) WHERE {
      VALUES ?genre { %s }
      ?x wdt:P136 ?genre; wdt:P106 wd:Q1028181; wdt:P27 wd:%s; wikibase:sitelinks ?sl.
      FILTER(?sl >= %d)
      %s
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    GROUP BY ?x ?xLabel
    ORDER BY DESC(?links)
    LIMIT %d
    """ % (values, nationality_qid, min_links, excl, limit * 2)
    try:
        rows = run_sparql(query, timeout=40)
    except Exception as e:
        print(f"genre_peers error: {e}")
        return []
    out, seen = [], set()
    for r in rows:
        q, label = qid(_v(r, "x")), _v(r, "xLabel")
        if q and label and not QID_RE.match(label) and q not in seen:
            seen.add(q)
            out.append({"qid": q, "label": label})
        if len(out) >= limit:
            break
    return out


def artist_work_stats(artist_id):
    """Aggregate over the artist's paintings: how many on Wikidata and the year
    span. Powers comparative sentences ('N works, 1902–1923')."""
    out = {"count": 0, "first": "", "last": ""}
    if not QID_RE.match(artist_id):
        return out
    query = """
    SELECT (COUNT(DISTINCT ?w) AS ?n) (MIN(?y) AS ?first) (MAX(?y) AS ?last) WHERE {
      ?w wdt:P170 wd:%s; wdt:P31 wd:Q3305213.
      OPTIONAL { ?w wdt:P571 ?date. BIND(YEAR(?date) AS ?y) }
    }
    """ % artist_id
    try:
        rows = run_sparql(query, timeout=30)
    except Exception as e:
        print(f"artist_work_stats error: {e}")
        return out
    if rows:
        r = rows[0]
        out["count"] = _v(r, "n")
        out["first"], out["last"] = _v(r, "first"), _v(r, "last")
    return out


# --------------------------------------------------------------------------- #
# Orchestrator                                                                 #
# --------------------------------------------------------------------------- #
def build_dossier(artwork_id, artist_id):
    """Gather a rich dossier; returns (artwork, artist) superset dicts."""
    try:
        artwork = artwork_facts(artwork_id)
    except Exception as e:
        print(f"artwork_facts error: {e}")
        artwork = {}
    try:
        artist = artist_facts(artist_id)
    except Exception as e:
        print(f"artist_facts error: {e}")
        artist = {}

    # One additive VALUES query for ALL multi-valued fields (artwork + artist).
    nb = related([
        (artwork_id, "depicts", "P180"),
        (artwork_id, "collection", "P195"),
        (artwork_id, "movementLinks", "P135"),
        (artist_id, "influencedBy", "P737"),
        (artist_id, "artistMovement", "P135"),
        (artist_id, "nationality", "P27"),
        (artist_id, "occupation", "P106"),
        (artist_id, "notableWork", "P800"),
        (artist_id, "education", "P69"),
        (artist_id, "teacher", "P1066"),
        (artist_id, "student", "P802"),
        (artist_id, "genre", "P136"),
        (artist_id, "award", "P166"),
        (artist_id, "memberOf", "P463"),
    ])
    if artwork:
        artwork["depicts"] = nb["depicts"][:10]
        artwork["collection"] = nb["collection"][:4]
        artwork["movementLinks"] = nb["movementLinks"][:3]
    if artist:
        artist["nationality"] = _labels_from(nb["nationality"], 3) or "Unknown"
        artist["movement"] = _labels_from(nb["artistMovement"], 3) or "Unknown"
        artist["occupation"] = _labels_from(nb["occupation"], 6)
        artist["notableWorks"] = _labels_from(nb["notableWork"], 6)
        artist["education"] = _labels_from(nb["education"], 3)
        artist["teachers"] = _labels_from(nb["teacher"], 4)
        artist["students"] = _labels_from(nb["student"], 5)
        artist["genres"] = _labels_from(nb["genre"], 4)
        artist["awards"] = _labels_from(nb["award"], 4)
        artist["memberships"] = _labels_from(nb["memberOf"], 4)
        artist["influencedBy"] = nb["influencedBy"][:6]
        movement_qids = [e["qid"] for e in nb["artistMovement"]]
        artist["contemporaries"] = contemporaries(
            movement_qids, artist.get("_birthYear"), artist_id
        )[:6]

    # Entity link map for in-article linking: proper-noun labels → QID. We link
    # only "named" entities (label has an uppercase letter), which neatly skips
    # generic concepts like "sky", "painter", "portrait" while keeping people,
    # places, institutions, movements, and works.
    link_map = {}

    def _add_entity(label, ent_qid, open_as=None):
        # open_as: "artist" or "artwork" marks an entity the explore view can open
        # in-app (the rest just link out to Wikidata).
        if not label or not ent_qid or not QID_RE.match(ent_qid):
            return
        if len(label) < 3 or not any(c.isupper() for c in label):
            return
        entry = {"label": label, "qid": ent_qid}
        if open_as:
            entry["open"] = open_as
        link_map.setdefault(label.lower(), entry)

    if artwork and artwork.get("title") and artwork["title"] != "Untitled":
        _add_entity(artwork["title"], artwork_id)
    if artist and artist.get("name") and artist["name"] != "Unknown artist":
        _add_entity(artist["name"], artist_id)
    # Cap per source — only entities that can plausibly appear in the prose (the
    # article is built from a similarly capped fact packet); keeps the map lean.
    for key in ("depicts", "collection", "movementLinks", "influencedBy",
                "artistMovement", "nationality", "occupation", "notableWork",
                "education", "teacher", "student", "genre", "award", "memberOf"):
        # Influences and teachers/students are artists → openable in the explore view.
        open_as = "artist" if key in ("influencedBy", "teacher", "student") else None
        for e in nb.get(key, [])[:10]:
            _add_entity(e.get("label"), e.get("qid"), open_as)
    for e in (artist.get("contemporaries") if artist else []) or []:
        _add_entity(e.get("label"), e.get("qid"), "artist")

    # Longest label first so multi-word names win over substrings when matching.
    entities = sorted(link_map.values(), key=lambda x: len(x["label"]), reverse=True)
    if artwork:
        artwork["_link_entities"] = entities

    return artwork, artist
