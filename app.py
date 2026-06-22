from flask import Flask, jsonify, request, make_response
import requests
from datetime import datetime
import json
import random
import re
import os
import threading
from collections import OrderedDict


def _load_dotenv(path=".env"):
    """Minimal .env loader (KEY=VALUE lines) so a local .env populates os.environ.

    Avoids a dependency; handles comments, blank lines, optional `export`, and
    surrounding quotes. Existing environment variables are not overwritten.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


_load_dotenv()

import article_writer  # noqa: E402  (imported after .env is loaded)
import sparql_library  # noqa: E402
import linked_art  # noqa: E402
import enrichment  # noqa: E402

QID_RE = re.compile(r'^Q\d+$')

app = Flask(__name__, static_folder='static')

# Trust the reverse proxy a managed host (Render/Cloud Run/Railway/nginx) puts in
# front of us, so request.remote_addr is the real client IP (correct rate-limit
# buckets) and request.host_url/scheme reflect the public https origin.
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# --- Rate limiting --------------------------------------------------------- #
# A generous per-IP default protects the Wikidata-hitting endpoints from abuse
# without throttling normal browsing (one item view fans out into several
# requests). The home page (/) and health check (/healthz) are exempt. In-memory
# storage suits our single-process Waitress server; point AOTD_RATELIMIT_STORAGE
# at redis:// if you ever run more than one process. AOTD_RATELIMIT_ENABLED=0
# disables it entirely.
from flask_limiter import Limiter  # noqa: E402
from flask_limiter.util import get_remote_address  # noqa: E402

_RL_ENABLED = os.environ.get("AOTD_RATELIMIT_ENABLED", "1").lower() not in ("0", "false", "no")
# Browsing one item fans out into several requests (gallery + per-item details +
# summary + prefetch), so the default has to be roomy or a normal session 429s
# itself. The health check (/healthz) and home page (/) are exempted outright.
_RL_DEFAULT = os.environ.get("AOTD_RATELIMIT_DEFAULT", "1200 per hour;120 per minute")
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=_RL_DEFAULT.split(";"),
    storage_uri=os.environ.get("AOTD_RATELIMIT_STORAGE", "memory://"),
    enabled=_RL_ENABLED,
)


@app.errorhandler(429)
def _ratelimited(e):
    return jsonify({
        "status": "error",
        "message": "Too many requests — please slow down and try again shortly.",
    }), 429


# --- Bounded in-process caches --------------------------------------------- #
# These caches are unbounded by nature (any valid QID combination is a new key),
# so without a cap a crawler or abuser hitting many distinct ids would grow
# memory until the process is OOM-killed. Cap each and evict the oldest entry
# (FIFO) once full. Caller must hold _cache_lock.
_MAX_CACHE = int(os.environ.get("AOTD_CACHE_MAX", "512"))


def _cache_set(cache, key, value):
    """Insert into a bounded process cache, evicting the oldest entry when full.
    Relies on dict/OrderedDict insertion order. Caller holds _cache_lock."""
    if key not in cache and len(cache) >= _MAX_CACHE:
        cache.popitem(last=False)
    cache[key] = value


WDQS_ENDPOINT = 'https://query.wikidata.org/sparql'
# Wikimedia asks for a descriptive User-Agent with a real contact. Override the
# contact via the AOTD_CONTACT env var when deploying.
_CONTACT = os.environ.get('AOTD_CONTACT', 'https://github.com/jchirum/artwork-of-the-day')
HEADERS = {'User-Agent': f'ArtworkOfTheDay/1.0 ({_CONTACT})'}

# Total paintings (Q3305213) on Wikidata that have both an image and a creator
# is ~390k. We pick a random offset below this to get variety while keeping the
# query fast. Kept conservative so a random offset never overruns the result set.
MAX_OFFSET = 300000

# The day's result is deterministic per calendar date, so cache aggressively
# in-process: the gallery is rebuilt at most once per date, and per-entity
# details are cached for the process lifetime. Repeat visits are then instant,
# and the site keeps working even if Wikidata is briefly slow/unavailable after
# the first successful fetch of the day.
_cache_lock = threading.Lock()
_day_cache = {"date": None, "payload": None}  # single entry; no cap needed
_details_cache = OrderedDict()
_article_cache = OrderedDict()
_enrichment_cache = OrderedDict()  # progressive enrichment payloads
_la_cache = OrderedDict()  # Linked Art records: (kind, qid) -> record dict


def run_sparql(query, timeout=45):
    """Execute a SPARQL query against Wikidata and return the result bindings."""
    response = requests.get(
        WDQS_ENDPOINT,
        params={'format': 'json', 'query': query},
        headers=HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json().get('results', {}).get('bindings', [])


def qid(uri):
    """Extract the bare Q-id from a Wikidata entity URI."""
    return uri.rsplit('/', 1)[-1] if uri else ''


def dedupe_by_artwork(paintings):
    """Drop duplicate artworks (e.g. an artwork that has more than one image)."""
    seen = set()
    unique = []
    for p in paintings:
        if p['artwork_id'] and p['artwork_id'] not in seen:
            seen.add(p['artwork_id'])
            unique.append(p)
    return unique


def commons_thumb(image_url, width=800):
    """Turn a Wikidata P18 (commons FilePath) URL into an https thumbnail URL."""
    if not image_url:
        return ""
    image_url = image_url.replace('http://', 'https://')
    sep = '&' if '?' in image_url else '?'
    return f"{image_url}{sep}width={width}"


def birthday_paintings(month, day):
    """Fetch paintings by painters born on a given month/day (any year).

    This is what ties the "artwork of the day" to today's date. The label
    SERVICE is omitted (it is the slow part); labels are fetched per-entity
    later. Results are ordered so the daily pick is stable across reloads.
    """
    query = """
    SELECT ?artwork ?image ?creator ?birth WHERE {
      ?creator wdt:P106 wd:Q1028181;     # occupation: painter
               wdt:P569 ?birth.          # date of birth
      FILTER(MONTH(?birth) = %d && DAY(?birth) = %d)
      ?artwork wdt:P170 ?creator;        # created by this artist
               wdt:P31 wd:Q3305213;      # instance of: painting
               wdt:P18 ?image.           # must have an image
    }
    ORDER BY ?artwork
    LIMIT 50
    """ % (month, day)

    results = run_sparql(query, timeout=45)
    paintings = []
    for r in results:
        paintings.append({
            'artwork_id': qid(r.get('artwork', {}).get('value', '')),
            'creator_id': qid(r.get('creator', {}).get('value', '')),
            'image': r.get('image', {}).get('value', ''),
            'birth': r.get('birth', {}).get('value', ''),
        })
    print(f"Found {len(paintings)} paintings for birthday {month:02d}-{day:02d}")
    return paintings


def get_random_paintings(rng):
    """Fallback: a batch of paintings starting at a (date-seeded) random offset.

    Used only when no painter born today has a painting on Wikidata, so the page
    is never empty. The label SERVICE is omitted for speed.
    """
    offset = rng.randint(0, MAX_OFFSET)
    query = """
    SELECT ?artwork ?image ?creator WHERE {
      ?artwork wdt:P31 wd:Q3305213;   # instance of: painting
               wdt:P18 ?image;        # must have an image
               wdt:P170 ?creator.     # must have a creator
    }
    LIMIT 50 OFFSET %d
    """ % offset

    results = run_sparql(query, timeout=45)
    paintings = []
    for r in results:
        paintings.append({
            'artwork_id': qid(r.get('artwork', {}).get('value', '')),
            'creator_id': qid(r.get('creator', {}).get('value', '')),
            'image': r.get('image', {}).get('value', ''),
            'birth': '',
        })
    print(f"Found {len(paintings)} fallback paintings at offset {offset}")
    return paintings


def gather_details(artwork_id, artist_id):
    """Rich artwork + artist dossier from the SPARQL library (sparql_library.py)."""
    return sparql_library.build_dossier(artwork_id, artist_id)


@app.route('/')
@limiter.exempt  # the home page is a health-check target + every visit's first hit; never throttle it
def index():
    return app.send_static_file('index.html')


@app.route('/healthz')
@limiter.exempt  # platform health checks poll this frequently; must never be rate limited
def healthz():
    """Lightweight liveness probe for Render/other hosts (no Wikidata, no model)."""
    return jsonify({"status": "ok"}), 200


# Most artworks to expose in the day's gallery.
MAX_GALLERY = 30


@app.route('/artwork-of-the-day', methods=['GET'])
def artwork_of_the_day():
    """Return the list of artworks connected to today's date.

    The frontend flips through them with arrows, fetching each artwork's
    details on demand from /artwork-details (so this endpoint stays fast).
    """
    try:
        today = datetime.now()
        date_key = today.strftime('%Y-%m-%d')

        # Serve the day's cached gallery if we already built it.
        with _cache_lock:
            if _day_cache["date"] == date_key:
                return jsonify(_day_cache["payload"])

        date_label = today.strftime('%B %d')  # e.g. "June 20"
        # Seed by date so ordering is stable for the whole day, but changes daily.
        rng = random.Random(date_key)

        # Connect the artwork to today's date by the artist's birthday. Fall back
        # to random paintings so the page is never empty on a rare quiet date.
        occasion = "birthday"
        paintings = birthday_paintings(today.month, today.day)
        if not paintings:
            occasion = "random"
            paintings = get_random_paintings(rng)

        paintings = dedupe_by_artwork(paintings)
        if not paintings:
            return jsonify({
                "status": "error",
                "error": "No paintings found",
                "message": "Could not find any paintings in the database",
            }), 404

        rng.shuffle(paintings)
        items = [{
            "artwork_id": p["artwork_id"],
            "creator_id": p["creator_id"],
            "image": commons_thumb(p["image"]),
            "birthYear": p.get("birth", "")[:4],
        } for p in paintings[:MAX_GALLERY]]

        payload = {
            "status": "success",
            "occasion": occasion,
            "today": date_label,
            "count": len(items),
            "items": items,
        }
        with _cache_lock:
            _day_cache["date"] = date_key
            _day_cache["payload"] = payload
        print(f"Built and cached {len(items)} items for {date_key} (occasion={occasion})")
        return jsonify(payload)

    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "error": "Timeout",
            "message": "Wikidata took too long to respond. Please try again.",
        }), 504
    except Exception as e:
        print(f"Critical error in artwork_of_the_day: {e}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "message": "An unexpected error occurred while fetching artwork",
        }), 500


@app.route('/artwork-details', methods=['GET'])
def artwork_details():
    """Return full details for one artwork + its artist (used while flipping)."""
    artwork_id = request.args.get('artwork', '')
    artist_id = request.args.get('artist', '')

    if not QID_RE.match(artwork_id) or not QID_RE.match(artist_id):
        return jsonify({
            "status": "error",
            "message": "Invalid or missing artwork/artist id",
        }), 400

    cache_key = (artwork_id, artist_id)
    cached = _details_cache.get(cache_key)
    if cached:
        return jsonify(cached)

    try:
        artwork, artist = gather_details(artwork_id, artist_id)
        if not artwork and not artist:
            return jsonify({
                "status": "error",
                "message": "No details found for this artwork",
            }), 404
        payload = {
            "status": "success",
            "artwork": artwork,
            "artist": artist,
        }
        with _cache_lock:
            _cache_set(_details_cache, cache_key, payload)
        return jsonify(payload)
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "Wikidata took too long to respond. Please try again.",
        }), 504
    except Exception as e:
        print(f"Error in artwork_details: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/artwork-article', methods=['GET'])
def artwork_article():
    """Return a short 'About' article for one artwork + its artist.

    Assembled deterministically from the Wikidata dossier — no model, no API key.
    Cached per artwork for the process lifetime.
    """
    artwork_id = request.args.get('artwork', '')
    artist_id = request.args.get('artist', '')

    if not QID_RE.match(artwork_id) or not QID_RE.match(artist_id):
        return jsonify({
            "status": "error",
            "message": "Invalid or missing artwork/artist id",
        }), 400

    cache_key = (artwork_id, artist_id)
    cached = _article_cache.get(cache_key)
    if cached:
        return jsonify(cached)

    try:
        # Reuse the details we already fetched for this item when possible.
        details = _details_cache.get((artwork_id, artist_id))
        if details:
            artwork = details.get("artwork") or {}
            artist = details.get("artist") or {}
        else:
            artwork, artist = gather_details(artwork_id, artist_id)

        article = article_writer.build(artwork, artist)
        payload = {"status": "success", "article": article}
        with _cache_lock:
            _cache_set(_article_cache, cache_key, payload)
        return jsonify(payload)
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "Wikidata took too long to respond. Please try again.",
        }), 504
    except Exception as e:
        print(f"Error in artwork_article: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/artwork-enrichment', methods=['GET'])
def artwork_enrichment():
    """Progressive enrichment layers (Wikipedia overview, other works, extra
    artwork facts, period/place framing) for the About panel. Fetched by the
    frontend AFTER the base article renders, so first paint stays fast. Cached
    per artwork for the process lifetime; reuses the warm details dossier."""
    artwork_id = request.args.get('artwork', '')
    artist_id = request.args.get('artist', '')

    if not QID_RE.match(artwork_id) or not QID_RE.match(artist_id):
        return jsonify({
            "status": "error",
            "message": "Invalid or missing artwork/artist id",
        }), 400

    cache_key = (artwork_id, artist_id)
    cached = _enrichment_cache.get(cache_key)
    if cached:
        return jsonify(cached)

    try:
        details = _details_cache.get((artwork_id, artist_id))
        if details:
            artwork = details.get("artwork") or {}
            artist = details.get("artist") or {}
        else:
            artwork, artist = gather_details(artwork_id, artist_id)

        data = enrichment.build(artwork_id, artist_id, artwork, artist)
        payload = {"status": "success", "enrichment": data}
        with _cache_lock:
            _cache_set(_enrichment_cache, cache_key, payload)
        return jsonify(payload)
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "A source took too long to respond. Please try again.",
        }), 504
    except Exception as e:
        print(f"Error in artwork_enrichment: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


_LA_CTX = linked_art.CONTEXT
_LA_CONTENT_TYPE = f'application/ld+json; charset=utf-8; profile="{_LA_CTX}"'


def _ld_response(record, status=200):
    """Serialize a plain JSON-LD body (used for error payloads)."""
    resp = make_response(json.dumps(record, ensure_ascii=False, indent=2), status)
    resp.headers["Content-Type"] = "application/ld+json; charset=utf-8"
    return resp


def _hal_links(self_uri):
    """The spec's non-semantic HAL '_links' envelope (added outside the
    schema-validated semantic body)."""
    return {
        "self": self_uri,
        "curies": [
            {"name": "la", "href": "https://linked.art/api/rels/1/{rel}", "templated": True}
        ],
        "la:modelVersion": {"href": "https://linked.art/model/1.0/", "name": "v1.0"},
        "la:apiVersion": {"href": "https://linked.art/api/1.0/", "name": "v1.0"},
    }


def _wants_html(req):
    """Content negotiation: HTML only when explicitly asked or strictly
    preferred over JSON-LD. Default (curl, no Accept) → JSON-LD."""
    fmt = (req.args.get("format") or "").lower()
    if fmt == "html":
        return True
    if fmt in ("json", "jsonld", "ld+json"):
        return False
    accept = req.accept_mimetypes
    html_q = accept["text/html"]
    json_q = max(accept["application/ld+json"], accept["application/json"])
    return html_q > json_q


def _render_la_html(record, self_uri):
    """A minimal human-readable HTML representation at the same URI."""
    import html as _html
    label = _html.escape(str(record.get("_label", "Record")))
    rtype = _html.escape(str(record.get("type", "")))
    desc = ""
    for r in record.get("referred_to_by", []):
        desc = _html.escape(r.get("content", ""))
        break
    pretty = _html.escape(json.dumps(record, indent=2, ensure_ascii=False))
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label} — Linked Art</title>
<style>body{{font-family:system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}}
h1{{margin-bottom:.2rem}} .t{{color:#666;font-size:.9rem}} pre{{background:#f5f5f5;padding:1rem;border-radius:8px;overflow:auto;font-size:.8rem}}
a{{color:#2c5d8a}}</style></head>
<body>
<h1>{label}</h1>
<p class="t">{rtype} · <a href="https://linked.art/">Linked Art</a> record</p>
<p>{desc}</p>
<p><a href="{self_uri}?format=jsonld">View JSON-LD ↗</a></p>
<pre>{pretty}</pre>
</body></html>"""
    resp = make_response(page)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return resp


def _serve_la(record, self_uri, status=200):
    """Serve a semantic Linked Art record: HTML or JSON-LD (with HAL envelope)
    per content negotiation."""
    if _wants_html(request):
        return _render_la_html(record, self_uri)
    body = dict(record)
    body["_links"] = _hal_links(self_uri)
    resp = make_response(json.dumps(body, ensure_ascii=False, indent=2), status)
    resp.headers["Content-Type"] = _LA_CONTENT_TYPE
    return resp


def _dossier_or_facts(qid):
    """(artwork, artist, artist_qid) — full dossier when a creator exists."""
    artist_qid = sparql_library.creator_of(qid)
    if artist_qid:
        artwork, artist = sparql_library.build_dossier(qid, artist_qid)
    else:
        artwork, artist = sparql_library.artwork_facts(qid), {}
    return artwork, artist, artist_qid


@app.route('/object/<qid>', methods=['GET'])
def linked_art_object(qid):
    """Linked Art (https://linked.art) HumanMadeObject record for an artwork."""
    self_uri = request.base_url
    if not QID_RE.match(qid):
        return _ld_response({"error": "invalid object id"}, 400)
    cached = _la_cache.get(("object", qid))
    if cached:
        return _serve_la(cached, self_uri)
    try:
        artwork, artist, artist_qid = _dossier_or_facts(qid)
        if not artwork:
            return _ld_response({"error": "object not found"}, 404)
        base = request.host_url.rstrip("/")
        person_uri = f"{base}/person/{artist_qid}" if artist_qid else None
        # Grounded Wikidata summary as the description.
        summary = article_writer.build(artwork, artist)
        desc = " ".join(p for s in summary.get("sections", []) for p in s.get("paragraphs", []))
        record = linked_art.object_record(
            artwork, artist, base=base,
            object_uri=f"{base}/object/{qid}", person_uri=person_uri,
            artwork_qid=qid, artist_qid=artist_qid, description=desc,
        )
        with _cache_lock:
            _cache_set(_la_cache, ("object", qid), record)
        return _serve_la(record, self_uri)
    except requests.exceptions.Timeout:
        return _ld_response({"error": "Wikidata timeout"}, 504)
    except Exception as e:
        print(f"Error in linked_art_object: {e}")
        return _ld_response({"error": str(e)}, 500)


@app.route('/visual/<qid>', methods=['GET'])
def linked_art_visual(qid):
    """Linked Art VisualItem record — the subjects an artwork depicts."""
    self_uri = request.base_url
    if not QID_RE.match(qid):
        return _ld_response({"error": "invalid visual id"}, 400)
    cached = _la_cache.get(("visual", qid))
    if cached:
        return _serve_la(cached, self_uri)
    try:
        artwork = sparql_library.artwork_facts(qid)
        if not artwork:
            return _ld_response({"error": "visual not found"}, 404)
        artwork["depicts"] = sparql_library.related([(qid, "depicts", "P180")]).get("depicts", [])
        base = request.host_url.rstrip("/")
        depicts_qids = [d["qid"] for d in artwork["depicts"] if d.get("qid")]
        entity_types = sparql_library.classify_entities(depicts_qids) if depicts_qids else {}
        record = linked_art.visual_record(
            artwork, visual_uri=f"{base}/visual/{qid}", artwork_qid=qid,
            entity_types=entity_types,
        )
        with _cache_lock:
            _cache_set(_la_cache, ("visual", qid), record)
        return _serve_la(record, self_uri)
    except requests.exceptions.Timeout:
        return _ld_response({"error": "Wikidata timeout"}, 504)
    except Exception as e:
        print(f"Error in linked_art_visual: {e}")
        return _ld_response({"error": str(e)}, 500)


@app.route('/person/<qid>', methods=['GET'])
def linked_art_person(qid):
    """Linked Art (https://linked.art) Person record for an artist."""
    self_uri = request.base_url
    if not QID_RE.match(qid):
        return _ld_response({"error": "invalid person id"}, 400)
    cached = _la_cache.get(("person", qid))
    if cached:
        return _serve_la(cached, self_uri)
    try:
        artist = sparql_library.artist_facts(qid)
        if not artist:
            return _ld_response({"error": "person not found"}, 404)
        base = request.host_url.rstrip("/")
        record = linked_art.person_record(
            artist, base=base, person_uri=f"{base}/person/{qid}", artist_qid=qid,
        )
        with _cache_lock:
            _cache_set(_la_cache, ("person", qid), record)
        return _serve_la(record, self_uri)
    except requests.exceptions.Timeout:
        return _ld_response({"error": "Wikidata timeout"}, 504)
    except Exception as e:
        print(f"Error in linked_art_person: {e}")
        return _ld_response({"error": str(e)}, 500)


@app.route('/place/<qid>', methods=['GET'])
def linked_art_place(qid):
    """Linked Art Place record (with WKT geometry + Getty TGN equivalent)."""
    self_uri = request.base_url
    if not QID_RE.match(qid):
        return _ld_response({"error": "invalid place id"}, 400)
    cached = _la_cache.get(("place", qid))
    if cached:
        return _serve_la(cached, self_uri)
    try:
        facts = sparql_library.place_facts(qid)
        if not facts:
            return _ld_response({"error": "place not found"}, 404)
        base = request.host_url.rstrip("/")
        record = linked_art.place_record(facts, place_uri=f"{base}/place/{qid}", place_qid=qid)
        with _cache_lock:
            _cache_set(_la_cache, ("place", qid), record)
        return _serve_la(record, self_uri)
    except requests.exceptions.Timeout:
        return _ld_response({"error": "Wikidata timeout"}, 504)
    except Exception as e:
        print(f"Error in linked_art_place: {e}")
        return _ld_response({"error": str(e)}, 500)


@app.route('/group/<qid>', methods=['GET'])
def linked_art_group(qid):
    """Linked Art Group record (museum/collection/institution)."""
    self_uri = request.base_url
    if not QID_RE.match(qid):
        return _ld_response({"error": "invalid group id"}, 400)
    cached = _la_cache.get(("group", qid))
    if cached:
        return _serve_la(cached, self_uri)
    try:
        facts = sparql_library.group_facts(qid)
        if not facts:
            return _ld_response({"error": "group not found"}, 404)
        base = request.host_url.rstrip("/")
        record = linked_art.group_record(facts, group_uri=f"{base}/group/{qid}", group_qid=qid)
        with _cache_lock:
            _cache_set(_la_cache, ("group", qid), record)
        return _serve_la(record, self_uri)
    except requests.exceptions.Timeout:
        return _ld_response({"error": "Wikidata timeout"}, 504)
    except Exception as e:
        print(f"Error in linked_art_group: {e}")
        return _ld_response({"error": str(e)}, 500)


@app.after_request
def add_header(response):
    """Cache only static assets in the browser; keep HTML + live JSON fresh.

    The page and the API endpoints must NOT be browser-cached: otherwise a code
    or payload-format change leaves clients serving stale HTML/data and breaking.
    Speed comes from the in-process caches (_day_cache/_details_cache/_article_cache),
    not the browser. Static files (the logo) are safe to cache for a day.
    """
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=86400'
    else:
        response.headers['Cache-Control'] = 'no-store'
    return response


if __name__ == "__main__":
    # Dev entry point. For production use a WSGI server: `python serve.py`.
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
