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
import concurrent.futures as _cf
import os

import requests

import sparql_library as S
import wikidata_facts as WF


def _gather(tasks, workers=5):
    """Run independent fetch callables concurrently (all are I/O-bound, so threads
    give real parallelism). Returns {key: result}; a failed task yields None.
    Capped at `workers` to stay polite to WDQS (≈5 concurrent queries per IP)."""
    results = {}
    with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(fn): key for key, fn in tasks.items()}
        for fut in _cf.as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                print(f"enrichment[{key}] failed: {e}")
                results[key] = None
    return results

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

    # ---- Seeds / ids computed up front ---------------------------------- #
    museum_qid = artwork.get("locationQid") or ((artwork.get("collection") or [{}])[0] or {}).get("qid")
    if museum_qid and not S.QID_RE.match(museum_qid):
        museum_qid = ""
    _title = (artwork.get("title") or "").lower()

    def _real_subject(d):
        label = d.get("label") or ""
        low = label.lower()
        if not d.get("qid") or not any(c.isupper() for c in label):
            return False  # named entities only (skips sky, figure, armrest…)
        if "depicted in" in low or "depicted on" in low or (_title and _title in low):
            return False  # skip Wikidata "person depicted in <painting>" placeholders
        return True

    depict_ids = [d["qid"] for d in (artwork.get("depicts") or []) if _real_subject(d)][:8]
    fact_ids = [q for q in (artwork_id, artist_id, museum_qid) if q and S.QID_RE.match(q)]
    gloss_ids = [q for q in (artwork.get("genreQid"), artist.get("birthPlaceQid"),
                             artist.get("deathPlaceQid")) if q]

    # ---- Step A: traditions first — it seeds the hop-2 lineage/peers ----- #
    trad = S.artist_traditions(artist_id)
    teacher_qids = [t["qid"] for t in trad["teachers"]]
    school_qids = [e["qid"] for e in trad["education"]]
    genre_qids = [g["qid"] for g in trad["genres"]]
    mv_qids = [m["qid"] for m in trad["movements"]]
    nat = trad["nationality"][0]["qid"] if trad["nationality"] else ""

    # ---- Step B: every remaining fetch runs concurrently ----------------- #
    tasks = {
        "awk_wiki": lambda: wikipedia_summary(S.wikipedia_sitelink(artwork_id)),
        "art_wiki": lambda: wikipedia_summary(_wiki_title(artist.get("wikipedia"))),
        "depict_cards": (lambda: S.expand_entities(depict_ids)) if depict_ids else (lambda: {}),
        "stmts": lambda: WF.statements_for(fact_ids),
        "works": lambda: S.artist_works(artist_id, exclude_qid=artwork_id, limit=8),
        "collections": lambda: S.artist_collections(artist_id, limit=8),
        "work_stats": lambda: S.artist_work_stats(artist_id),
        "relations": lambda: S.artist_relations(artist_id),
        "ids": lambda: S.artist_identifiers(artist_id),
        "ctx": lambda: S.artwork_context(artwork_id),
        "place_gloss": (lambda: S.enrich_entities(gloss_ids)) if gloss_ids else (lambda: {}),
        "museum_card": (lambda: S.expand_entities([museum_qid]).get(museum_qid)) if museum_qid else (lambda: None),
        "cohort": (lambda: S.notable_by_property(teacher_qids, "P1066", exclude=artist_id, limit=6)) if teacher_qids else (lambda: []),
        "alumni": (lambda: S.notable_by_property(school_qids, "P69", exclude=artist_id, limit=6, painter_only=True)) if school_qids else (lambda: []),
        "peers": (lambda: S.genre_peers(genre_qids, nat, exclude=artist_id, limit=6)) if (genre_qids and nat) else (lambda: []),
        "members": (lambda: S.notable_by_property(mv_qids, "P135", exclude=artist_id, limit=6)) if mv_qids else (lambda: []),
    }
    R = _gather(tasks, workers=5)

    def g(key, default):
        v = R.get(key)
        return default if v is None else v

    all_stmts = g("stmts", {})

    # ==== Assemble sections (pure, no I/O) =============================== #
    # 1. Wikipedia overview — artwork first (most specific), then the artist.
    awk = R.get("awk_wiki")
    if awk:
        sections.append({"heading": "About the painting", "paragraphs": [awk["extract"]]})
        add_source(f"Wikipedia: {awk['title']}", awk["url"])
    art = R.get("art_wiki")
    if art:
        sections.append({"heading": f"About {name}", "paragraphs": [art["extract"]]})
        add_source(f"Wikipedia: {art['title']}", art["url"])

    # 1b. The subjects in depth (expanded depicted entities).
    cards = g("depict_cards", {})
    if depict_ids and cards:
        phrases = [p for p in (_phrase_card(cards[q]) for q in depict_ids if q in cards) if p]
        if phrases:
            sections.append({"heading": "A closer look at the subjects",
                             "paragraphs": ["The painting brings together " + _and_list(phrases) + "."]})

    # 1c. The work in detail (generic narration of the artwork's statements).
    work_facts = WF.narrate(all_stmts.get(artwork_id, []), limit=8)
    if work_facts:
        sections.append({"heading": "The work in detail", "paragraphs": work_facts})

    # 2. Other works by the artist (+ documented output).
    works = g("works", [])
    if works:
        phrases = [f"{w['label']} ({w['year']})" if w["year"] else w["label"] for w in works[:6]]
        paras = [f"Other paintings by {name} include " + _and_list(phrases) + "."]
        stats = g("work_stats", {})
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

    # 2b. Collections that hold the artist's work (+ comparative).
    collections = g("collections", [])
    if collections:
        labels = [c["label"] for c in collections]
        held = [f"Beyond this piece, {name}'s paintings are held in collections including "
                + _and_list(labels) + "."]
        top = collections[0]
        if top["n"] >= 2:
            held.append(f"Their work is most concentrated at {top['label']}, "
                        f"which holds {top['n']} of their paintings.")
        sections.append({"heading": "Where the artist's work is held", "paragraphs": held})

    # 2c. Genre, movement & training tradition (what it is).
    tradition = []
    for gg in trad["genres"][:2]:
        if gg.get("description"):
            tradition.append(f"{name} worked in {gg['label']} — {gg['description']}.")
    for m in trad["movements"][:2]:
        d = f" — {m['description']}" if m.get("description") else ""
        tradition.append(f"The work belongs to the {m['label']} movement{d}.")
    for ed in trad["education"][:2]:
        if ed.get("description"):
            tradition.append(f"{name} trained at {ed['label']}, {_a_or_an(ed['description'])} {ed['description']}.")
    if tradition:
        sections.append({"heading": "Genre and tradition", "paragraphs": tradition[:4]})

    # 2d. Lineage & peers (hop-2, ranked by notability; clickable artists).
    def _people(rel_label_phrase, items):
        if not items:
            return None
        for e in items:
            entities.append({"label": e["label"], "qid": e["qid"], "open": "artist"})
        return rel_label_phrase + _and_list([e["label"] for e in items]) + "."

    lineage = []
    if teacher_qids:
        s = _people(f"In {trad['teachers'][0]['label']}'s circle of pupils, {name} trained alongside ", g("cohort", []))
        if s:
            lineage.append(s)
    if school_qids:
        s = _people(f"Fellow students at the {trad['education'][0]['label']} included ", g("alumni", []))
        if s:
            lineage.append(s)
    if genre_qids and nat:
        s = _people("Other notable painters working in the same genre include ", g("peers", []))
        if s:
            lineage.append(s)
    if mv_qids:
        s = _people("Other artists of the movement include ", g("members", []))
        if s:
            lineage.append(s)
    if lineage:
        sections.append({"heading": "Lineage and peers", "paragraphs": lineage})

    # 2e. The museum, in depth (founding + generic narration).
    if museum_qid:
        museum_paras = []
        mcard = R.get("museum_card")
        if mcard:
            inc = (mcard.get("inception") or "")[:4]
            if inc.isdigit():
                mlabel, desc = mcard.get("label") or "the museum", mcard.get("description")
                sent = f"Founded in {inc}, {mlabel}"
                sent += f" is {_a_or_an(desc)} {desc}." if desc else "."
                museum_paras.append(sent)
        museum_paras += WF.narrate(all_stmts.get(museum_qid, []), skip={"P571"}, limit=4)
        if museum_paras:
            sections.append({"heading": "The museum", "paragraphs": museum_paras})

    # 2f. More about the artist (generic facts beyond the basics).
    artist_skip = {"P166", "P463", "P69", "P1066", "P802", "P737", "P106", "P27",
                   "P135", "P136", "P800", "P569", "P570", "P19", "P20", "P26"}
    more_artist = WF.narrate(all_stmts.get(artist_id, []), skip=artist_skip, limit=6)
    if more_artist:
        sections.append({"heading": "More about the artist", "paragraphs": more_artist})

    # 3. Richer artwork facts (exhibitions / events / movement).
    ctx = g("ctx", {"exhibitions": [], "events": [], "movements": []})
    facts = []
    if ctx["movements"]:
        facts.append(f"It is associated with the {_and_list(ctx['movements'][:3])} movement.")
    if ctx["events"]:
        facts.append(f"It relates to {_and_list(ctx['events'][:3])}.")
    if ctx["exhibitions"]:
        facts.append(f"It has featured in exhibitions including {_and_list(ctx['exhibitions'][:4])}.")
    if facts:
        sections.append({"heading": "Exhibitions and context", "paragraphs": facts})

    # 4. Period & place framing.
    framing = []
    era = _era((artwork.get("creationDateRaw") or "")[:4] or artwork.get("creationDate"))
    title = artwork.get("title")
    if era and title and title != "Untitled":
        framing.append(f"{title} dates from {era}.")
    info = g("place_gloss", {})
    gq = artwork.get("genreQid")
    if gq and info.get(gq, {}).get("description"):
        gc = info[gq]
        framing.append(f"As a work of {gc['label']}, it belongs to {gc['description']}.")
    for key in ("birthPlaceQid", "deathPlaceQid"):
        q = artist.get(key)
        d = info.get(q) if q else None
        if d and d.get("description"):
            framing.append(f"{d['label']} is {_a_or_an(d['description'])} {d['description']}.")
    if framing:
        sections.append({"heading": "Period and place", "paragraphs": framing})

    # 5. Circle and legacy.
    rel = g("relations", {"spouse": [], "students": [], "influenced": []})
    circle = []
    if rel["spouse"]:
        circle.append(f"{name} was married to {_and_list(rel['spouse'])}.")
    if rel["students"]:
        circle.append(f"Among {name}'s students were {_and_list(rel['students'][:6])}.")
    if rel["influenced"]:
        circle.append(f"Artists who cite {name} as an influence include {_and_list(rel['influenced'][:6])}.")
    if circle:
        sections.append({"heading": "Circle and legacy", "paragraphs": circle})

    # 6. Research links (authority files).
    ids = g("ids", {})
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
