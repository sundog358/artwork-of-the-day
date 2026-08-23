"""Integration tests for the network-facing parsers — HTTP fully mocked.

These exercise the code that turns raw API responses into our shapes: the
Wikibase REST statement/qualifier/reference parsing, and the SPARQL bindings
parsing. No real requests are made (the `responses` library intercepts them),
so they're deterministic and fast.
"""

import responses

import app as APP
import enrichment as E
import iiif as IIIF
import linked_art as LA
import sparql_library as S
import wikibase_rest as WR
import wikidata_facts as WF

REST = "https://www.wikidata.org/w/rest.php/wikibase/v1"


def test_person_record_carries_authority_equivalents():
    """VIAF / Getty ULAN / RKD / ISNI ids become `equivalent` references."""
    artist = {
        "name": "Leonardo da Vinci",
        "viaf": "24604287",
        "ulan": "500010879",
        "isni": "0000 0001 2122 5050",
    }
    rec = LA.person_record(
        artist, base="https://x.org", person_uri="https://x.org/person/Q762", artist_qid="Q762"
    )
    ids = {e["id"] for e in rec["equivalent"]}
    assert "http://www.wikidata.org/entity/Q762" in ids
    assert "https://viaf.org/viaf/24604287" in ids
    assert "http://vocab.getty.edu/ulan/500010879" in ids
    assert "https://isni.org/isni/0000000121225050" in ids  # spaces stripped


def test_concept_record_links_wikidata_and_aat():
    rec = LA.concept_record(
        {"name": "Italian Renaissance", "description": "art movement", "aat": "300021140"},
        concept_uri="https://x.org/concept/Q1474884",
        concept_qid="Q1474884",
    )
    assert rec["type"] == "Type"
    ids = {e["id"] for e in rec["equivalent"]}
    assert "http://vocab.getty.edu/aat/300021140" in ids


def test_set_record_is_a_collection():
    rec = LA.set_record(
        {"name": "Louvre", "description": "museum in Paris"},
        set_uri="https://x.org/set/Q19675",
        set_qid="Q19675",
    )
    assert rec["type"] == "Set"
    assert "Collection of Louvre" in rec["_label"]
    assert rec["classified_as"][0]["id"].endswith("/300025976")


def test_object_record_models_events_member_of_and_iiif():
    artwork = {
        "title": "Mona Lisa",
        "image": "https://commons.wikimedia.org/wiki/Special:FilePath/Mona%20Lisa.jpg?width=800",
        "collection": [{"qid": "Q19675", "label": "Louvre"}],
        "activities": [{"kind": "event", "label": "theft", "start": "1911", "end": "1913"}],
    }
    rec = LA.object_record(
        artwork,
        {},
        base="https://x.org",
        object_uri="https://x.org/object/Q12418",
        person_uri=None,
        artwork_qid="Q12418",
        artist_qid="",
    )
    assert rec["member_of"][0]["id"] == "https://x.org/set/Q19675"
    assert rec["used_for"][0]["_label"] == "theft"
    assert rec["used_for"][0]["timespan"]["begin_of_the_begin"].startswith("1911")
    # representation carries both the image and the IIIF manifest pointer
    shown = rec["representation"][0]["digitally_shown_by"]
    assert any("/iiif/Q12418/manifest.json" in d["access_point"][0]["id"] for d in shown)


@responses.activate
def test_iiif_manifest_builds_from_commons_imageinfo():
    responses.add(
        responses.GET,
        IIIF._COMMONS_API,
        json={
            "query": {
                "pages": {
                    "-1": {
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/x/Mona_Lisa.jpg",
                                "width": 7601,
                                "height": 11348,
                                "mime": "image/jpeg",
                            }
                        ]
                    }
                }
            }
        },
    )
    man = IIIF.manifest(
        title="Mona Lisa",
        manifest_uri="https://x.org/iiif/Q12418/manifest.json",
        image_filepath="https://commons.wikimedia.org/wiki/Special:FilePath/Mona%20Lisa.jpg?width=800",
        metadata=[("Artist", "Leonardo da Vinci"), ("Date", "")],
    )
    canvas = man["items"][0]
    assert man["type"] == "Manifest"
    assert (canvas["width"], canvas["height"]) == (7601, 11348)
    assert canvas["items"][0]["items"][0]["body"]["id"].endswith("Mona_Lisa.jpg")
    assert canvas["items"][0]["items"][0]["body"]["format"] == "image/jpeg"
    assert len(man["metadata"]) == 1  # empty Date dropped


@responses.activate
def test_iiif_manifest_declares_the_commons_media_type_not_jpeg():
    """A non-JPEG Commons file must not be described as image/jpeg.

    Commons serves PNG, TIFF and WebP under the same Special:FilePath shape, so
    a hardcoded format silently mislabels the body and a strict viewer is
    entitled to reject it.
    """
    IIIF._info_cache.clear()
    responses.add(
        responses.GET,
        IIIF._COMMONS_API,
        json={
            "query": {
                "pages": {
                    "-1": {
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/x/Sunflowers.png",
                                "width": 1200,
                                "height": 1600,
                                "mime": "image/png",
                            }
                        ]
                    }
                }
            }
        },
    )
    man = IIIF.manifest(
        title="Sunflowers",
        manifest_uri="https://x.org/iiif/Q1/manifest.json",
        image_filepath="https://commons.wikimedia.org/wiki/Special:FilePath/Sunflowers.png",
    )
    assert man["items"][0]["items"][0]["items"][0]["body"]["format"] == "image/png"


def test_image_format_falls_back_to_the_extension_then_to_jpeg():
    """The API is the source of truth, the extension is the backstop."""
    assert IIIF._image_format("image/tiff", "https://x/a.png") == "image/tiff"
    assert IIIF._image_format(None, "https://x/a.png") == "image/png"
    assert IIIF._image_format("text/html", "https://x/a.webp") == "image/webp"
    assert IIIF._image_format(None, "https://x/no-extension") == "image/jpeg"


def test_linked_art_endpoints_send_cors_and_vary():
    """Linked Art records are open cross-origin data — an invalid id short-circuits
    to a 400 (no network) and must still carry the CORS + Vary headers."""
    APP.app.config["TESTING"] = True
    client = APP.app.test_client()
    resp = client.get("/object/notaqid")
    assert resp.status_code == 400
    assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    assert "Accept" in (resp.headers.get("Vary") or "")
    assert resp.headers.get("Content-Type", "").startswith("application/ld+json")


@responses.activate
def test_dated_statements_parses_start_and_end_qualifiers():
    responses.add(
        responses.GET,
        f"{REST}/entities/items/Q1/statements",
        json={
            "P793": [
                {
                    "value": {"type": "value", "content": "Q9"},
                    "qualifiers": [
                        {
                            "property": {"id": "P585"},
                            "value": {"content": {"time": "+1911-08-21T00:00:00Z"}},
                        },
                        {
                            "property": {"id": "P582"},
                            "value": {"content": {"time": "+1913-12-10T00:00:00Z"}},
                        },
                    ],
                }
            ]
        },
    )
    assert WR.dated_statements("Q1", "P793") == [{"qid": "Q9", "start": "1911", "end": "1913"}]


@responses.activate
def test_dated_statements_skips_novalue_and_string_values():
    responses.add(
        responses.GET,
        f"{REST}/entities/items/Q1/statements",
        json={
            "P793": [
                {"value": {"type": "novalue"}},
                {"value": {"type": "value", "content": "a string, not a qid"}},
            ]
        },
    )
    assert WR.dated_statements("Q1", "P793") == []


@responses.activate
def test_dated_facts_resolves_value_labels():
    responses.add(
        responses.GET,
        f"{REST}/entities/items/Q1/statements",
        json={
            "P793": [
                {
                    "value": {"type": "value", "content": "Q9"},
                    "qualifiers": [
                        {
                            "property": {"id": "P585"},
                            "value": {"content": {"time": "+1911-01-01T00:00:00Z"}},
                        }
                    ],
                }
            ]
        },
    )
    responses.add(responses.GET, f"{REST}/entities/items/Q9/labels", json={"en": "theft"})
    assert WR.dated_facts("Q1", "P793") == [("theft", "1911", "")]


@responses.activate
def test_reference_sources_ranks_by_count_and_filters_noise():
    def stmt(src):
        return {
            "value": {"type": "value", "content": "Qx"},
            "references": [{"parts": [{"property": {"id": "P248"}, "value": {"content": src}}]}],
        }

    responses.add(
        responses.GET,
        f"{REST}/entities/items/Q1/statements",
        json={"P1": [stmt("Q54919"), stmt("Q54919")], "P2": [stmt("Q15241312")]},
    )
    responses.add(responses.GET, f"{REST}/entities/items/Q54919/labels", json={"en": "VIAF"})
    responses.add(
        responses.GET, f"{REST}/entities/items/Q15241312/labels", json={"en": "Freebase Data Dumps"}
    )
    # VIAF kept (most-cited); Freebase filtered as import-artefact noise.
    assert WR.reference_sources("Q1", limit=5) == ["VIAF"]


@responses.activate
def test_rest_get_returns_none_on_error_status():
    responses.add(responses.GET, f"{REST}/entities/items/Q1/labels", status=404)
    assert WR.labels(["Q1"]) == {}


@responses.activate
def test_statements_for_parses_sparql_bindings():
    responses.add(
        responses.GET,
        S.WDQS_ENDPOINT,
        json={
            "results": {
                "bindings": [
                    {
                        "subj": {"value": "http://www.wikidata.org/entity/Q1"},
                        "prop": {"value": "http://www.wikidata.org/entity/P186"},
                        "propLabel": {"value": "made from material"},
                        "v": {"value": "http://www.wikidata.org/entity/Q296955"},
                        "vLabel": {"value": "oil paint"},
                    }
                ]
            }
        },
    )
    out = WF.statements_for(["Q1"])
    assert out["Q1"] == [
        {"pid": "P186", "prop": "made from material", "value": "oil paint", "vqid": "Q296955"}
    ]


@responses.activate
def test_artist_works_requires_label_and_dedupes():
    responses.add(
        responses.GET,
        S.WDQS_ENDPOINT,
        json={
            "results": {
                "bindings": [
                    {
                        "w": {"value": "http://www.wikidata.org/entity/Q2"},
                        "wLabel": {"value": "Tea Leaves"},
                        "date": {"value": "1909-01-01T00:00:00Z"},
                    },
                    {
                        "w": {"value": "http://www.wikidata.org/entity/Q3"},
                        "wLabel": {"value": "Q3"},
                    },  # label === qid → dropped
                ]
            }
        },
    )
    works = S.artist_works("Q1", exclude_qid="", limit=8)
    assert works == [{"qid": "Q2", "label": "Tea Leaves", "year": "1909"}]


def _binding(**cols):
    return {k: {"value": v} for k, v in cols.items()}


@responses.activate
def test_expand_entities_builds_cards():
    responses.add(
        responses.GET,
        S.WDQS_ENDPOINT,
        json={
            "results": {
                "bindings": [
                    _binding(
                        e="http://www.wikidata.org/entity/Q90",
                        eLabel="Paris",
                        eDescription="capital of France",
                        t="city",
                        country="France",
                    ),
                ]
            }
        },
    )
    cards = S.expand_entities(["Q90"])
    assert cards["Q90"]["label"] == "Paris"
    assert cards["Q90"]["description"] == "capital of France"
    assert cards["Q90"]["country"] == "France"


@responses.activate
def test_notable_by_property_ranks_and_filters_qid_labels():
    responses.add(
        responses.GET,
        S.WDQS_ENDPOINT,
        json={
            "results": {
                "bindings": [
                    _binding(
                        x="http://www.wikidata.org/entity/Q173223",
                        xLabel="Mary Cassatt",
                        links="40",
                    ),
                    _binding(
                        x="http://www.wikidata.org/entity/Q999", xLabel="Q999", links="3"
                    ),  # qid label dropped
                ]
            }
        },
    )
    out = S.notable_by_property(["Q1"], "P1066", exclude="Q1", limit=6)
    assert out == [{"qid": "Q173223", "label": "Mary Cassatt"}]


@responses.activate
def test_artist_collections_returns_labels_with_counts():
    responses.add(
        responses.GET,
        S.WDQS_ENDPOINT,
        json={
            "results": {
                "bindings": [
                    _binding(cLabel="Museum of Fine Arts Boston", n="6"),
                    _binding(cLabel="Metropolitan Museum of Art", n="1"),
                ]
            }
        },
    )
    cols = S.artist_collections("Q1", limit=8)
    assert cols[0] == {"label": "Museum of Fine Arts Boston", "n": 6}


@responses.activate
def test_wikipedia_sitelinks_extracts_titles():
    responses.add(
        responses.GET,
        S.WDQS_ENDPOINT,
        json={
            "results": {
                "bindings": [
                    _binding(
                        e="http://www.wikidata.org/entity/Q90",
                        article="https://en.wikipedia.org/wiki/Paris",
                    ),
                ]
            }
        },
    )
    assert S.wikipedia_sitelinks(["Q90"]) == {"Q90": "Paris"}


@responses.activate
def test_artwork_context_groups_by_relation():
    responses.add(
        responses.GET,
        S.WDQS_ENDPOINT,
        json={
            "results": {
                "bindings": [
                    _binding(rel="movements", vLabel="Italian Renaissance"),
                    _binding(rel="events", vLabel="theft"),
                ]
            }
        },
    )
    ctx = S.artwork_context("Q1")
    assert ctx["movements"] == ["Italian Renaissance"]
    assert ctx["events"] == ["theft"]


def test_enrichment_build_degrades_when_one_parallel_task_fails(monkeypatch):
    """A failed enrichment layer should drop that section, not the whole article."""
    artwork = {
        "title": "Mona Lisa",
        "creationDate": "1503",
        "creationDateRaw": "1503-01-01T00:00:00Z",
        "genreQid": "Q134307",
        "location": "Louvre",
        "locationQid": "Q19675",
        "depicts": [{"qid": "Q762", "label": "Lisa del Giocondo"}],
    }
    artist = {
        "name": "Leonardo da Vinci",
        "birthdate": "1452",
        "birthPlaceQid": "Q83233",
        "wikipedia": "https://en.wikipedia.org/wiki/Leonardo_da_Vinci",
    }

    monkeypatch.setattr(
        S,
        "artist_traditions",
        lambda q: {
            "teachers": [],
            "education": [],
            "genres": [{"qid": "Q134307", "label": "portrait", "description": "genre of art"}],
            "movements": [],
            "nationality": [],
        },
    )
    monkeypatch.setattr(S, "wikipedia_sitelink", lambda qid: "Mona_Lisa")
    monkeypatch.setattr(
        E,
        "wikipedia_summary",
        lambda title: (
            {
                "title": title,
                "extract": f"{title} summary.",
                "url": f"https://en.wikipedia.org/wiki/{title}",
            }
            if title
            else None
        ),
    )
    monkeypatch.setattr(E, "_context_wikipedia", lambda qids: [])
    monkeypatch.setattr(WR, "dated_facts", lambda qid, pid: [])
    monkeypatch.setattr(WR, "reference_sources", lambda qid, limit=5: ["VIAF"])
    monkeypatch.setattr(S, "notable_artworks_by", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        S,
        "expand_entities",
        lambda qids: {
            "Q762": {"label": "Lisa del Giocondo", "description": "Florentine noblewoman"},
            "Q19675": {"label": "Louvre", "description": "art museum"},
        },
    )
    monkeypatch.setattr(
        S,
        "enrich_entities",
        lambda qids: {
            "Q134307": {"label": "portrait", "description": "genre of art"},
            "Q83233": {"label": "Vinci", "description": "town in Tuscany"},
        },
    )
    monkeypatch.setattr(
        WF,
        "statements_for",
        lambda qids: {
            "Q12418": [
                {
                    "pid": "P186",
                    "prop": "made from material",
                    "value": "oil paint",
                    "vqid": "Q296955",
                }
            ],
            "Q762": [],
            "Q19675": [],
        },
    )

    def broken_artist_works(*args, **kwargs):
        raise RuntimeError("artist works query failed")

    monkeypatch.setattr(S, "artist_works", broken_artist_works)
    monkeypatch.setattr(S, "artist_collections", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        S, "artist_work_stats", lambda qid: {"count": "4", "first": "1503", "last": "1519"}
    )
    monkeypatch.setattr(
        S, "artist_relations", lambda qid: {"spouse": [], "students": [], "influenced": []}
    )
    monkeypatch.setattr(S, "artist_identifiers", lambda qid: {"viaf": "24604287"})
    monkeypatch.setattr(
        S, "artwork_context", lambda qid: {"movements": [], "events": [], "exhibitions": []}
    )
    monkeypatch.setattr(S, "notable_by_property", lambda *args, **kwargs: [])
    monkeypatch.setattr(S, "genre_peers", lambda *args, **kwargs: [])

    out = E.build("Q12418", "Q762", artwork, artist)
    headings = [section["heading"] for section in out["sections"]]

    assert "About the painting" in headings
    assert "The work in detail" in headings
    assert "Other works by the artist" not in headings
    assert out["provenance"] == ["VIAF"]
    assert out["sources"][0]["label"] == "Wikipedia: Mona_Lisa"
    assert out["links"] == [{"label": "VIAF", "url": "https://viaf.org/viaf/24604287"}]
