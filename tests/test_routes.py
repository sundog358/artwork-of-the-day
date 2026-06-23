"""Route-level / end-to-end tests via the Flask test client.

The data layer (Wikidata SPARQL, the Wikibase REST API, Commons) is stubbed with
monkeypatch, so these exercise the real route logic — response assembly, status
codes, content types, HAL + CORS headers, content negotiation, and error paths —
with zero network. Complements test_integration.py (which covers the parsers).
"""

import io

import pytest
from PIL import Image

import app as APP
import iiif
import sparql_library as SL
import wikibase_rest as WR


@pytest.fixture
def client():
    APP.app.config["TESTING"] = True
    APP.limiter.enabled = False
    for c in (
        APP._la_cache,
        APP._details_cache,
        APP._article_cache,
        APP._resolve_cache,
        APP._date_cache,
        APP._share_cache,
    ):
        c.clear()
    APP._day_cache["date"] = None
    APP._day_cache["payload"] = None
    return APP.app.test_client()


# --- canned fixtures -------------------------------------------------------- #
ARTWORK = {
    "title": "Mona Lisa",
    "creationDate": "1503",
    "creationDateRaw": "1503-01-01T00:00:00Z",
    "genre": "portrait",
    "genreQid": "Q134307",
    "medium": "oil paint",
    "mediumQid": "Q296955",
    "heightCm": "77",
    "widthCm": "53",
    "location": "Louvre",
    "locationQid": "Q19675",
    "inventory": "INV. 779",
    "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Mona%20Lisa.jpg?width=800",
    "collection": [{"qid": "Q19675", "label": "Louvre"}],
    "depicts": [],
}
ARTIST = {
    "name": "Leonardo da Vinci",
    "description": "Italian Renaissance polymath",
    "birthdate": "1452",
    "birthDateRaw": "1452-04-15T00:00:00Z",
    "birthplace": "Vinci",
    "birthPlaceQid": "Q83233",
    "viaf": "24604287",
    "ulan": "500010879",
}
PLACE = {
    "name": "Paris",
    "description": "capital of France",
    "wkt": "POINT(2.35 48.85)",
    "tgn": "7008038",
}
GROUP = {
    "name": "Louvre",
    "description": "art museum in Paris",
    "inception": "1793-08-10T00:00:00Z",
    "ulan": "500125247",
}
CONCEPT = {"name": "portrait", "description": "genre of art", "aat": "300015637"}


# --- liveness / static ------------------------------------------------------ #
def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_home_serves_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Artwork of the Day" in r.data


# --- gallery / explore ------------------------------------------------------ #
def test_artwork_of_the_day_builds_gallery(client, monkeypatch):
    monkeypatch.setattr(
        APP,
        "birthday_paintings",
        lambda m, d: [
            {
                "artwork_id": "Q12418",
                "creator_id": "Q762",
                "image": "http://x/a.jpg",
                "birth": "1452-04-15",
            },
        ],
    )
    r = client.get("/artwork-of-the-day")
    body = r.get_json()
    assert r.status_code == 200
    assert body["status"] == "success" and body["count"] == 1
    assert body["items"][0]["artwork_id"] == "Q12418"
    assert body["items"][0]["image"].startswith("https://")  # commons_thumb upgraded the scheme


def test_artwork_of_the_day_rejects_bad_date(client):
    r = client.get("/artwork-of-the-day?month=99&day=99")
    assert r.status_code == 400
    assert r.get_json()["status"] == "error"


def test_resolve_requires_a_valid_id(client):
    assert client.get("/resolve").status_code == 400
    assert client.get("/resolve?artist=not-a-qid").status_code == 400


def test_resolve_artist_returns_item(client, monkeypatch):
    monkeypatch.setattr(
        APP,
        "_resolve_artist",
        lambda q: {"artwork_id": "Q12418", "creator_id": q, "image": "https://x/a.jpg"},
    )
    r = client.get("/resolve?artist=Q762")
    assert r.status_code == 200
    assert r.get_json()["item"]["creator_id"] == "Q762"


# --- details / article ------------------------------------------------------ #
def test_artwork_details_validates_ids(client):
    assert client.get("/artwork-details?artwork=Q1&artist=bad").status_code == 400


def test_artwork_details_returns_dossier(client, monkeypatch):
    monkeypatch.setattr(APP, "gather_details", lambda aw, ar: (ARTWORK, ARTIST))
    r = client.get("/artwork-details?artwork=Q12418&artist=Q762")
    body = r.get_json()
    assert r.status_code == 200
    assert body["artwork"]["title"] == "Mona Lisa"
    assert body["artist"]["name"] == "Leonardo da Vinci"


def test_artwork_article_is_deterministic_sections(client, monkeypatch):
    monkeypatch.setattr(APP, "gather_details", lambda aw, ar: (ARTWORK, ARTIST))
    r = client.get("/artwork-article?artwork=Q12418&artist=Q762")
    article = r.get_json()["article"]
    assert r.status_code == 200
    assert article["mode"] == "wikidata"  # never an LLM
    assert any(s.get("paragraphs") for s in article["sections"])


# --- Linked Art -------------------------------------------------------------- #
def _assert_la_headers(r):
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    assert "Accept" in r.headers.get("Vary", "")
    assert r.headers["Content-Type"].startswith("application/ld+json")


def test_object_record_full_envelope(client, monkeypatch):
    monkeypatch.setattr(SL, "creator_of", lambda q: "Q762")
    monkeypatch.setattr(SL, "build_dossier", lambda aw, ar: (dict(ARTWORK), dict(ARTIST)))
    monkeypatch.setattr(WR, "dated_facts", lambda q, p: [])
    r = client.get("/object/Q12418")
    body = r.get_json()
    assert r.status_code == 200
    _assert_la_headers(r)
    assert body["@context"] == "https://linked.art/ns/v1/linked-art.json"
    assert body["type"] == "HumanMadeObject"
    assert body["_links"]["self"].endswith(
        "/object/Q12418"
    )  # HAL envelope, outside the semantic body
    assert body["member_of"][0]["type"] == "Set"


def test_object_content_negotiation_html(client, monkeypatch):
    monkeypatch.setattr(SL, "creator_of", lambda q: "Q762")
    monkeypatch.setattr(SL, "build_dossier", lambda aw, ar: (dict(ARTWORK), dict(ARTIST)))
    monkeypatch.setattr(WR, "dated_facts", lambda q, p: [])
    r = client.get("/object/Q12418?format=html")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/html")
    assert b"Mona Lisa" in r.data


def test_object_404_when_absent(client, monkeypatch):
    monkeypatch.setattr(SL, "creator_of", lambda q: "")
    monkeypatch.setattr(SL, "artwork_facts", lambda q: {})
    r = client.get("/object/Q999999")
    assert r.status_code == 404
    _assert_la_headers(r)


def test_person_record_with_authorities(client, monkeypatch):
    monkeypatch.setattr(SL, "artist_facts", lambda q: dict(ARTIST))
    r = client.get("/person/Q762")
    body = r.get_json()
    assert r.status_code == 200 and body["type"] == "Person"
    ids = {e["id"] for e in body["equivalent"]}
    assert "https://viaf.org/viaf/24604287" in ids


def test_place_and_group_and_concept_and_set(client, monkeypatch):
    monkeypatch.setattr(SL, "place_facts", lambda q: dict(PLACE))
    monkeypatch.setattr(SL, "group_facts", lambda q: dict(GROUP))
    monkeypatch.setattr(SL, "concept_facts", lambda q: dict(CONCEPT))
    assert client.get("/place/Q90").get_json()["type"] == "Place"
    assert client.get("/group/Q19675").get_json()["type"] == "Group"
    assert client.get("/concept/Q134307").get_json()["type"] == "Type"
    s = client.get("/set/Q19675").get_json()
    assert s["type"] == "Set" and "Collection of Louvre" in s["_label"]


# --- IIIF -------------------------------------------------------------------- #
def test_iiif_manifest_route(client, monkeypatch):
    monkeypatch.setattr(SL, "artwork_facts", lambda q: dict(ARTWORK))
    monkeypatch.setattr(SL, "creator_of", lambda q: "Q762")
    monkeypatch.setattr(SL, "artist_facts", lambda q: dict(ARTIST))
    monkeypatch.setattr(iiif, "image_info", lambda url: ("https://upload/x.jpg", 7601, 11348))
    r = client.get("/iiif/Q12418/manifest.json")
    body = r.get_json()
    assert r.status_code == 200
    assert r.headers["Access-Control-Allow-Origin"] == "*"
    assert 'profile="http://iiif.io/api/presentation/3/context.json"' in r.headers["Content-Type"]
    assert body["type"] == "Manifest"
    assert (body["items"][0]["width"], body["items"][0]["height"]) == (7601, 11348)


def test_iiif_404_without_image(client, monkeypatch):
    monkeypatch.setattr(SL, "artwork_facts", lambda q: {"title": "x", "image": ""})
    r = client.get("/iiif/Q12418/manifest.json")
    assert r.status_code == 404


# --- Social share / link previews ------------------------------------------- #
def test_share_route_renders_per_artwork_og_tags(client, monkeypatch):
    monkeypatch.setattr(SL, "creator_of", lambda q: "Q762")
    monkeypatch.setattr(SL, "build_dossier", lambda aw, ar: (dict(ARTWORK), dict(ARTIST)))
    r = client.get("/a/Q12418")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/html")
    # title + og:title carry the painting + artist, server-side (crawler-visible)
    assert "<title>Mona Lisa — Leonardo da Vinci · Meta History Book</title>" in body
    # og:image points at the branded 1200×630 card route, not the logo
    assert "/og/Q12418.jpg" in body
    assert "/a/Q12418" in body  # canonical + og:url
    assert 'property="og:image:width" content="1200"' in body
    assert 'property="og:image:height" content="630"' in body


def test_share_route_bad_id_serves_plain_app(client):
    r = client.get("/a/not-a-qid")
    assert r.status_code == 200
    assert "Meta History Book" in r.get_data(as_text=True)  # the default SPA, no per-artwork OG


def test_og_card_route_renders_1200x630_jpeg(client, monkeypatch):
    monkeypatch.setattr(SL, "creator_of", lambda q: "Q762")
    monkeypatch.setattr(SL, "build_dossier", lambda aw, ar: (dict(ARTWORK), dict(ARTIST)))
    # feed the card a synthetic painting so no network is touched
    buf = io.BytesIO()
    Image.new("RGB", (400, 600), (90, 80, 70)).save(buf, "JPEG")

    class _Resp:
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(APP.requests, "get", lambda *a, **k: _Resp())
    r = client.get("/og/Q12418.jpg")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.data)).size == (1200, 630)


def test_og_card_bad_id_redirects_to_logo(client):
    r = client.get("/og/not-a-qid.jpg")
    assert r.status_code in (301, 302)
    assert "8sprocket" in r.headers.get("Location", "")
