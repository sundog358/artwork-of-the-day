"""Unit tests for the deterministic, pure logic — no network, fast.

Covers the bits that turn Wikidata data into prose: the generic property
narrator, the date/qualifier formatters, the entity-card phrasing, and the
small SPARQL value helpers. These are exactly the functions whose correctness
the article's trustworthiness depends on, and they're all pure, so they test
cleanly without hitting any API.
"""
import enrichment as E
import sparql_library as S
import wikibase_rest as WR
import wikidata_facts as WF


# --------------------------------------------------------------------------- #
# wikidata_facts.narrate — the generic property → sentence engine             #
# --------------------------------------------------------------------------- #
def _st(pid, prop, value, vqid="Q1"):
    return {"pid": pid, "prop": prop, "value": value, "vqid": vqid}


def test_narrate_uses_registry_template():
    out = WF.narrate([_st("P186", "made from material", "oil paint")])
    assert out == ["Painted in oil paint."]


def test_narrate_collapses_multivalued_property():
    out = WF.narrate([
        _st("P186", "made from material", "oil paint"),
        _st("P186", "made from material", "poplar panel"),
    ])
    assert out == ["Painted in oil paint and poplar panel."]


def test_narrate_generic_fallback_for_unknown_property():
    out = WF.narrate([_st("P9999", "some property", "a value", vqid="")])
    assert out == ["Some property: a value."]


def test_narrate_drops_denylisted_property():
    # P31 (instance of) is on the denylist — should produce nothing.
    assert WF.narrate([_st("P31", "instance of", "painting")]) == []


def test_narrate_respects_caller_skip():
    assert WF.narrate([_st("P186", "made from material", "oil paint")], skip={"P186"}) == []


def test_narrate_orders_by_priority():
    out = WF.narrate([
        _st("P6216", "copyright status", "public domain"),  # priority 40
        _st("P186", "made from material", "oil paint"),      # priority 10
    ])
    assert out[0].startswith("Painted in")  # lower priority number sorts first


def test_narrate_drops_unitless_numbers_and_urls():
    assert WF.narrate([_st("P2048", "height", "79.4", vqid="")]) == []  # also denied, doubly safe
    assert WF.narrate([_st("P856", "official website", "http://x", vqid="")]) == []


def test_fmt_formats_year_only_dates():
    # Year-only precision (YYYY-01-01) → just the year.
    assert WF._fmt({"value": "1503-01-01T00:00:00Z", "vqid": ""}) == "1503"


# --------------------------------------------------------------------------- #
# wikibase_rest — qualifier year extraction                                   #
# --------------------------------------------------------------------------- #
def test_rest_year_from_time_qualifier():
    assert WR._year({"time": "+1911-08-21T00:00:00Z"}) == "1911"
    assert WR._year({"time": "+1519-01-01T00:00:00Z"}) == "1519"


def test_rest_year_handles_missing():
    assert WR._year(None) == ""
    assert WR._year({"amount": "5"}) == ""


# --------------------------------------------------------------------------- #
# enrichment — prose helpers                                                   #
# --------------------------------------------------------------------------- #
def test_and_list():
    assert E._and_list([]) == ""
    assert E._and_list(["a"]) == "a"
    assert E._and_list(["a", "b"]) == "a and b"
    assert E._and_list(["a", "b", "c"]) == "a, b, and c"


def test_date_phrase_span_and_single():
    assert E._date_phrase("theft", "1911", "1913") == "theft (1911–1913)"
    assert E._date_phrase("award", "1903", "") == "award (1903)"
    assert E._date_phrase("event", "", "") == "event"


def test_era_from_year():
    assert E._era(1910) == "the early 20th century"
    assert E._era(1503) == "the early 16th century"
    assert E._era("not a year") == ""


def test_a_or_an():
    assert E._a_or_an("museum") == "a"
    assert E._a_or_an("art museum") == "an"


def test_phrase_card_prefers_description_with_years():
    card = {"label": "HMS Fox", "description": "sixth-rate frigate",
            "birth": "1773-01-01", "death": "", "type": "ship"}
    assert E._phrase_card(card) == "HMS Fox (sixth-rate frigate, b. 1773)"


def test_phrase_card_label_only_when_no_detail():
    assert E._phrase_card({"label": "Untitled"}) == "Untitled"


# --------------------------------------------------------------------------- #
# sparql_library — value helpers                                              #
# --------------------------------------------------------------------------- #
def test_format_date_collapses_year_only():
    assert S.format_date("1910-01-01T00:00:00Z") == "1910"


def test_format_date_keeps_real_dates():
    assert S.format_date("1869-06-22T00:00:00Z") == "June 22, 1869"


def test_format_date_unknown():
    assert S.format_date("") == "Unknown"


def test_qid_extracts_bare_id():
    assert S.qid("http://www.wikidata.org/entity/Q12418") == "Q12418"
    assert S.qid("") == ""


def test_commons_thumb_builds_https_thumbnail():
    url = S.commons_thumb("http://commons.wikimedia.org/wiki/Special:FilePath/Foo.jpg")
    assert url.startswith("https://")
    assert "width=800" in url
