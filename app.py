from flask import Flask, jsonify, request
import requests
from datetime import datetime
import random
import re
import os
import threading


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

QID_RE = re.compile(r'^Q\d+$')

app = Flask(__name__, static_folder='static')

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
_day_cache = {"date": None, "payload": None}
_details_cache = {}
_article_cache = {}


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
def index():
    return app.send_static_file('index.html')


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
            "aiEnabled": bool(article_writer.openai_key()),
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
            _details_cache[cache_key] = payload
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
    """Return a short, grounded article about one artwork + its artist.

    Generation is gated on AOTD_OPENAI_API_KEY; without it (or on any failure)
    a deterministic, trivially-grounded fact summary is returned instead. Cached
    per artwork for the process lifetime.
    """
    artwork_id = request.args.get('artwork', '')
    artist_id = request.args.get('artist', '')

    if not QID_RE.match(artwork_id) or not QID_RE.match(artist_id):
        return jsonify({
            "status": "error",
            "message": "Invalid or missing artwork/artist id",
        }), 400

    # OpenAI is only used when explicitly requested (?generate=1). The default is
    # the deterministic Wikidata fact summary — no model call.
    generate = request.args.get('generate') == '1'
    cache_key = (artwork_id, artist_id, generate)
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

        article = article_writer.build(artwork, artist, generate=generate)
        payload = {"status": "success", "article": article}
        with _cache_lock:
            _article_cache[cache_key] = payload
        return jsonify(payload)
    except requests.exceptions.Timeout:
        return jsonify({
            "status": "error",
            "message": "Wikidata took too long to respond. Please try again.",
        }), 504
    except Exception as e:
        print(f"Error in artwork_article: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


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
