"""Integration tests for the network-facing parsers — HTTP fully mocked.

These exercise the code that turns raw API responses into our shapes: the
Wikibase REST statement/qualifier/reference parsing, and the SPARQL bindings
parsing. No real requests are made (the `responses` library intercepts them),
so they're deterministic and fast.
"""

import responses

import sparql_library as S
import wikibase_rest as WR
import wikidata_facts as WF

REST = "https://www.wikidata.org/w/rest.php/wikibase/v1"


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
