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
    """Format an ISO date string into a human-readable date."""
    if not date_str:
        return "Unknown"
    try:
        from datetime import datetime
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime("%B %d, %Y")
    except Exception:
        return date_str


def _v(row, key):
    return row.get(key, {}).get("value", "")


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
    SELECT ?artworkLabel ?date ?genreLabel ?mediumLabel ?height ?width ?locationLabel
           ?inventory ?commissionedByLabel ?seriesLabel ?creationPlaceLabel
           ?countryLabel ?mainSubjectLabel WHERE {
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
        "genre": _v(r, "genreLabel") or "Unknown",
        "medium": _v(r, "mediumLabel") or "Unknown",
        "dimensions": dimensions or "Unknown",
        "location": _v(r, "locationLabel") or "Unknown",
        "inventory": _v(r, "inventory"),
        "commissionedBy": _v(r, "commissionedByLabel"),
        "series": _v(r, "seriesLabel"),
        "creationPlace": _v(r, "creationPlaceLabel"),
        "country": _v(r, "countryLabel"),
        "mainSubject": _v(r, "mainSubjectLabel"),
    }


# --------------------------------------------------------------------------- #
# Artist biography (single scoped query, multi-valued fields concatenated)     #
# --------------------------------------------------------------------------- #
def artist_facts(artist_id):
    """Scalar (single-valued) artist fields only — fast. Multi-valued bio fields
    are fetched as additive rows in build_dossier() to avoid GROUP_CONCAT
    cartesian blow-up on prolific artists."""
    query = """
    SELECT ?artistLabel ?artistDescription ?birth ?death ?birthPlaceLabel
           ?deathPlaceLabel ?image ?article WHERE {
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
        "birthplace": _v(r, "birthPlaceLabel"),
        "deathdate": format_date(death) if death else "",
        "deathplace": _v(r, "deathPlaceLabel"),
        "image": commons_thumb(_v(r, "image")),
        "wikipedia": _v(r, "article"),
        "_birthYear": birth_year,
    }


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

    return artwork, artist
