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


def _phrase_card(card):
    """Narrate one expanded entity card as 'Label (description, years)'. The
    property->phrase building block the layers share."""
    label = (card.get("label") or "").strip()
    if not label:
        return ""
    desc = (card.get("description") or "").strip()
    by = (card.get("birth") or card.get("inception") or "")[:4]
    dy = (card.get("death") or "")[:4]
    years = ""
    if by.isdigit() and by not in desc:
        years = f"{by}–{dy}" if dy.isdigit() else f"b. {by}"
    detail = desc or (card.get("type") or "").strip()
    if detail and detail.lower() not in label.lower():
        return f"{label} ({detail}{', ' + years if years else ''})"
    return f"{label}{' (' + years + ')' if years else ''}"


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

    # --- 1b. The subjects in depth (depicted entities, expanded) ---------- #
    # Generic engine: expand every named depicted subject into a card and narrate
    # it — turns "depicts X, Y" into "X (a 1773 frigate), Y (an 18th-c warship)".
    depicts = artwork.get("depicts") or []
    _title = (artwork.get("title") or "").lower()

    def _real_subject(d):
        label = d.get("label") or ""
        low = label.lower()
        if not d.get("qid") or not any(c.isupper() for c in label):
            return False  # named entities only (skips sky, figure, armrest…)
        # Skip Wikidata placeholder items like "person depicted in <painting>".
        if "depicted in" in low or "depicted on" in low or (_title and _title in low):
            return False
        return True

    depict_ids = [d["qid"] for d in depicts if _real_subject(d)]
    if depict_ids:
        cards = S.expand_entities(depict_ids[:8])
        phrases = [_phrase_card(cards[q]) for q in depict_ids if q in cards]
        phrases = [p for p in phrases if p]
        if phrases:
            sections.append({
                "heading": "A closer look at the subjects",
                "paragraphs": ["The painting brings together " + _and_list(phrases) + "."],
            })

    # --- 2. Other works by the artist (+ documented output) --------------- #
    works = S.artist_works(artist_id, exclude_qid=artwork_id, limit=8)
    if works:
        phrases = [f"{w['label']} ({w['year']})" if w["year"] else w["label"] for w in works[:6]]
        paras = [f"Other paintings by {name} include " + _and_list(phrases) + "."]
        stats = S.artist_work_stats(artist_id)
        try:
            cnt = int(stats.get("count") or 0)
        except ValueError:
            cnt = 0
        first, last = stats.get("first", ""), stats.get("last", "")
        if cnt >= 3 and first and last and first != last:
            paras.append(f"In all, {name} has {cnt} documented paintings on Wikidata, "
                         f"spanning {first}–{last}.")
        sections.append({"heading": "Other works by the artist", "paragraphs": paras})
        for w in works[:6]:
            entities.append({"label": w["label"], "qid": w["qid"], "open": "artwork"})

    # --- 2b. Collections that hold the artist's work (+ comparative) ------ #
    collections = S.artist_collections(artist_id, limit=8)
    if collections:
        labels = [c["label"] for c in collections]
        held = [f"Beyond this piece, {name}'s paintings are held in collections including "
                + _and_list(labels) + "."]
        top = collections[0]
        if top["n"] >= 2:
            held.append(f"Their work is most concentrated at {top['label']}, "
                        f"which holds {top['n']} of their paintings.")
        sections.append({"heading": "Where the artist's work is held", "paragraphs": held})

    # --- 2c. Genre, movement & training tradition ------------------------- #
    trad = S.artist_traditions(artist_id)
    tradition = []
    for g in trad["genres"][:2]:
        if g.get("description"):
            tradition.append(f"{name} worked in {g['label']} — {g['description']}.")
    for m in trad["movements"][:2]:
        d = f" — {m['description']}" if m.get("description") else ""
        tradition.append(f"The work belongs to the {m['label']} movement{d}.")
    for ed in trad["education"][:2]:
        if ed.get("description"):
            tradition.append(f"{name} trained at {ed['label']}, {_a_or_an(ed['description'])} {ed['description']}.")
    if tradition:
        sections.append({"heading": "Genre and tradition", "paragraphs": tradition[:4]})

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

    # --- 5. Circle and legacy --------------------------------------------- #
    rel = S.artist_relations(artist_id)
    circle = []
    if rel["spouse"]:
        circle.append(f"{name} was married to {_and_list(rel['spouse'])}.")
    if rel["students"]:
        circle.append(f"Among {name}'s students were {_and_list(rel['students'][:6])}.")
    if rel["influenced"]:
        circle.append(f"Artists who cite {name} as an influence include {_and_list(rel['influenced'][:6])}.")
    if circle:
        sections.append({"heading": "Circle and legacy", "paragraphs": circle})

    # --- 6. Research links (authority files) ------------------------------ #
    ids = S.artist_identifiers(artist_id)
    links = []
    if ids.get("viaf"):
        links.append({"label": "VIAF", "url": f"https://viaf.org/viaf/{ids['viaf']}"})
    if ids.get("ulan"):
        links.append({"label": "Getty ULAN", "url": f"https://vocab.getty.edu/page/ulan/{ids['ulan']}"})
    if ids.get("rkd"):
        links.append({"label": "RKD", "url": f"https://rkd.nl/en/explore/artists/{ids['rkd']}"})
    if ids.get("loc"):
        links.append({"label": "Library of Congress", "url": f"https://id.loc.gov/authorities/names/{ids['loc']}"})
    if ids.get("commons"):
        links.append({"label": "Wikimedia Commons",
                      "url": "https://commons.wikimedia.org/wiki/Category:" + ids["commons"].replace(" ", "_")})

    return {"sections": sections, "otherWorks": works, "sources": sources,
            "entities": entities, "links": links}
