"""Progressive enrichment for the About panel.

Heavier, source-diverse context fetched AFTER the base (instant) article renders,
so first paint stays fast. Four layers, all grounded:

  1. Wikipedia overview — the lead summary for the artist (always) and the
     artwork (when it has a page). Verbatim from Wikipedia's own summary, so it is
     attributed (CC BY-SA) and linked.
  2. Other works by the artist — other paintings (P170), as a linkable list.
  3. Richer artwork facts — exhibition history (P608), commemorated events
     (P793), art movement (P135), surfaced when present.
  4. Period & place framing — the era derived from the creation year, plus
     one-line glosses for the genre and the artist's birth/death places.

`build(artwork_id, artist_id, artwork, artist)` returns:
    {"sections": [{heading, paragraphs:[…]}], "otherWorks": [{qid,label,year}],
     "sources": [{label, url}], "entities": [{label, qid}]}
The frontend appends the sections (reusing the same renderer + entity linking).
"""
import os

import requests

import sparql_library as S

_CONTACT = os.environ.get("AOTD_CONTACT", "https://metahistorybook.com")
_UA = {"User-Agent": f"ArtworkOfTheDay/1.0 ({_CONTACT})"}
_WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"


def _a_or_an(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _and_list(labels):
    labels = [l for l in labels if l]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


_ORDINALS = {1: "st", 2: "nd", 3: "rd"}


def _ordinal(n):
    return f"{n}{_ORDINALS.get(n if n < 20 else n % 10, 'th')}"


def _era(year):
    """Map a year to a readable era, e.g. 1910 -> 'the early 20th century'."""
    try:
        year = int(year)
    except (TypeError, ValueError):
        return ""
    century = (year - 1) // 100 + 1
    pos = (year - 1) % 100
    band = "early" if pos < 33 else "mid" if pos < 66 else "late"
    return f"the {band} {_ordinal(century)} century"


def wikipedia_summary(title):
    """Fetch the Wikipedia lead summary for an article title (or None)."""
    if not title:
        return None
    try:
        r = requests.get(_WIKI_SUMMARY + title, headers=_UA, timeout=12)
    except Exception as e:
        print(f"wikipedia_summary error: {e}")
        return None
    if r.status_code != 200:
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    if d.get("type") == "disambiguation":
        return None
    extract = (d.get("extract") or "").strip()
    if not extract:
        return None
    url = ((d.get("content_urls") or {}).get("desktop") or {}).get("page") \
        or f"https://en.wikipedia.org/wiki/{title}"
    return {"title": d.get("title") or title.replace("_", " "), "extract": extract, "url": url}


def _wiki_title(url):
    """https://en.wikipedia.org/wiki/Foo_Bar -> 'Foo_Bar'."""
    return url.rsplit("/wiki/", 1)[-1] if url and "/wiki/" in url else ""


def build(artwork_id, artist_id, artwork, artist):
    artwork = artwork or {}
    artist = artist or {}
    name = artist.get("name") or "the artist"
    sections, sources, entities = [], [], []
    seen_src = set()

    def add_source(label, url):
        if url and url not in seen_src:
            seen_src.add(url)
            sources.append({"label": label, "url": url})

    # --- 1. Wikipedia overview -------------------------------------------- #
    # Artwork first (most specific), then the artist.
    awk = wikipedia_summary(S.wikipedia_sitelink(artwork_id))
    if awk:
        sections.append({"heading": "About the painting", "paragraphs": [awk["extract"]]})
        add_source(f"Wikipedia: {awk['title']}", awk["url"])

    art = wikipedia_summary(_wiki_title(artist.get("wikipedia")))
    if art:
        sections.append({"heading": f"About {name}", "paragraphs": [art["extract"]]})
        add_source(f"Wikipedia: {art['title']}", art["url"])

    # --- 2. Other works by the artist ------------------------------------- #
    works = S.artist_works(artist_id, exclude_qid=artwork_id, limit=8)
    if works:
        phrases = [f"{w['label']} ({w['year']})" if w["year"] else w["label"] for w in works[:6]]
        sections.append({
            "heading": "Other works by the artist",
            "paragraphs": [f"Other paintings by {name} include " + _and_list(phrases) + "."],
        })
        for w in works[:6]:
            entities.append({"label": w["label"], "qid": w["qid"]})

    # --- 3. Richer artwork facts ------------------------------------------ #
    ctx = S.artwork_context(artwork_id)
    facts = []
    if ctx["movements"]:
        facts.append(f"It is associated with the {_and_list(ctx['movements'][:3])} movement.")
    if ctx["events"]:
        facts.append(f"It relates to {_and_list(ctx['events'][:3])}.")
    if ctx["exhibitions"]:
        facts.append(f"It has featured in exhibitions including {_and_list(ctx['exhibitions'][:4])}.")
    if facts:
        sections.append({"heading": "Exhibitions and context", "paragraphs": facts})

    # --- 4. Period & place framing ---------------------------------------- #
    framing = []
    era = _era((artwork.get("creationDateRaw") or "")[:4] or artwork.get("creationDate"))
    title = artwork.get("title")
    if era and title and title != "Untitled":
        framing.append(f"{title} dates from {era}.")
    # Gloss the genre and the artist's birth/death places (one batched query).
    gloss_ids = [q for q in (artwork.get("genreQid"), artist.get("birthPlaceQid"),
                             artist.get("deathPlaceQid")) if q]
    info = S.enrich_entities(gloss_ids) if gloss_ids else {}
    gq = artwork.get("genreQid")
    if gq and info.get(gq, {}).get("description"):
        g = info[gq]
        framing.append(f"As a work of {g['label']}, it belongs to {g['description']}.")
    for key in ("birthPlaceQid", "deathPlaceQid"):
        q = artist.get(key)
        d = info.get(q) if q else None
        if d and d.get("description"):
            framing.append(f"{d['label']} is {_a_or_an(d['description'])} {d['description']}.")
    if framing:
        sections.append({"heading": "Period and place", "paragraphs": framing})

    return {"sections": sections, "otherWorks": works, "sources": sources, "entities": entities}
