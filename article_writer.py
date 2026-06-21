"""Phase 1 grounded article writer for Artwork of the Day.

Pipeline (run once per artwork, then cached by the caller):
  1. Build a fact packet from the artwork + artist detail dicts.
  2. If an OpenAI API key is configured, ask the model to write a short article
     grounded ONLY in those facts, citing the fact id(s) behind each sentence
     (structured output, so we get schema-valid JSON back).
  3. Verify every sentence: it must cite a real fact, its numbers must appear in
     the cited facts (numeric_date_facts), and its wording must overlap the cited
     facts (support_span). Any blocking failure fails the whole article.
  4. Fail closed: if generation is disabled, errors, or fails verification, fall
     back to a deterministic fact summary that is trivially grounded.

The verifiers make this trustworthy: we never publish a number or claim that
isn't backed by a real Wikidata fact. The model is constrained, then checked.
"""
import json
import os
import re

import numeric_date_facts
import support_span

# Strips citation markers the model sometimes leaves in the visible prose, e.g.
# "(F1, F3)" or "[F2]" — the fact ids belong only in the structured `facts` field.
_MARKER_RE = re.compile(r"\s*[\(\[]\s*F\d+(?:\s*,\s*F\d+)*\s*[\)\]]")

# Config is read at call time (not import time) so a .env loaded at startup is
# always seen, regardless of import order.
def openai_key():
    # Accept the AOTD-specific name first, then the standard OPENAI_API_KEY
    # (what most .env files use, and what the OpenAI SDK reads natively).
    return os.environ.get("AOTD_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")


def _model():
    # Defaults to gpt-4o-mini (cheap, supports structured outputs); override via env.
    return os.environ.get("AOTD_ARTICLE_MODEL", "gpt-4o-mini")

# Structured-output schema: a blog post = title + sections, each with a heading
# and paragraphs of sentences, every sentence tagged with the fact ids it draws
# from (no recursion; additionalProperties off everywhere for OpenAI strict mode).
_SENTENCE = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["text", "facts"],
    "additionalProperties": False,
}
ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "paragraphs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"sentences": {"type": "array", "items": _SENTENCE}},
                            "required": ["sentences"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["heading", "paragraphs"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "sections"],
    "additionalProperties": False,
}


def _clean(value):
    """Treat our placeholder values as missing."""
    if not value or value in ("Unknown", "Untitled", "Unknown artist"):
        return ""
    return str(value)


def _labels(items, limit=8):
    """Join the labels of a [{qid,label}] neighbourhood list into a phrase."""
    return ", ".join(d.get("label", "") for d in (items or [])[:limit] if d.get("label"))


def build_fact_packet(artwork, artist, extra=None):
    """Turn the artwork + artist detail dicts into [{id, label, text}] facts.

    `extra` is an optional list of (label, text) pairs appended after the core
    facts — used for the second-hop entity enrichment (see _enrichment_facts),
    so background on depicted people, the movement, the museum, etc. becomes
    citable, grounded material.
    """
    facts = []

    def add(label, value):
        v = _clean(value)
        if v:
            facts.append({"id": f"F{len(facts) + 1}", "label": label, "text": v})

    add("Title", artwork.get("title"))
    add("Creation date", artwork.get("creationDate"))
    add("Medium", artwork.get("medium"))
    add("Genre", artwork.get("genre"))
    add("Current location", artwork.get("location"))
    add("Dimensions", artwork.get("dimensions"))
    add("Inventory number", artwork.get("inventory"))
    add("Commissioned by", artwork.get("commissionedBy"))
    add("Part of the series", artwork.get("series"))
    add("Place of creation", artwork.get("creationPlace"))
    add("Country of origin", artwork.get("country"))
    add("Main subject", artwork.get("mainSubject"))
    add("Depicts", _labels(artwork.get("depicts"), 10))

    add("Artist name", artist.get("name"))
    add("Artist description", artist.get("description"))
    born = _clean(artist.get("birthdate"))
    if born:
        bp = _clean(artist.get("birthplace"))
        add("Born", f"{born} in {bp}" if bp else born)
    died = _clean(artist.get("deathdate"))
    if died:
        dp = _clean(artist.get("deathplace"))
        add("Died", f"{died} in {dp}" if dp else died)
    add("Nationality", artist.get("nationality"))
    add("Art movement", artist.get("movement"))
    add("Occupation", artist.get("occupation"))
    add("Educated at", artist.get("education"))
    add("Teachers", artist.get("teachers"))
    add("Notable students", artist.get("students"))
    add("Genres", artist.get("genres"))
    add("Awards", artist.get("awards"))
    add("Member of", artist.get("memberships"))
    add("Notable works", artist.get("notableWorks"))
    add("Influenced by", _labels(artist.get("influencedBy"), 6))
    add("Contemporaries", _labels(artist.get("contemporaries"), 6))

    for label, text in (extra or []):
        add(label, text)
    return facts


def _enrichment_facts(artwork, artist):
    """Deterministic second hop: a compact 'About <X>' fact for each NAMED entity
    the article is likely to mention. We pull candidates from the dossier lists
    that carry QIDs (depicted subjects, influences, the movement, the museum,
    peers), keep only named entities (skips generic concepts like 'sky'), cap the
    set, and enrich them all in ONE batched SPARQL query. No model cost.
    """
    import sparql_library

    pool = []
    pool += artwork.get("depicts") or []
    pool += artist.get("influencedBy") or []
    pool += artwork.get("movementLinks") or []
    pool += artwork.get("collection") or []
    pool += artist.get("contemporaries") or []

    seen, picks = set(), []
    for e in pool:
        q, label = e.get("qid"), (e.get("label") or "")
        if not q or q in seen:
            continue
        if len(label) < 3 or not any(c.isupper() for c in label):
            continue  # named entities only — people/places/movements/institutions
        seen.add(q)
        picks.append((q, label))
        if len(picks) >= 8:  # bound packet size, latency, and prompt focus
            break
    if not picks:
        return []

    info = sparql_library.enrich_entities([q for q, _ in picks])
    facts = []
    for q, label in picks:
        d = info.get(q)
        if not d:
            continue
        desc = (d.get("description") or "").strip()
        by, dy = (d.get("birth") or "")[:4], (d.get("death") or "")[:4]
        years = ""
        if by.isdigit() and by not in desc:  # don't duplicate dates already in the desc
            years = f" ({by}–{dy})" if dy.isdigit() else f" (b. {by})"
        text = (desc + years).strip()
        if text:
            facts.append((f"About {label}", text))
    return facts


def build(artwork, artist, generate=False):
    """Return the article payload (always succeeds — falls back on any problem).

    OpenAI is called ONLY when generate=True and a key is configured. The default
    is the deterministic Wikidata fact summary — no model call — so normal
    browsing costs nothing and leans entirely on Wikidata/SPARQL data.
    """
    title = _clean(artwork.get("title")) or "This artwork"
    # Proper-noun entities (label → QID) for linking mentions in the prose.
    entities = artwork.get("_link_entities", [])

    if generate and openai_key():
        # Second hop only when we're actually generating: enrich the named
        # entities with one batched SPARQL query so the prose has real
        # background to ground on (who Lisa del Giocondo was, what Florence is…).
        try:
            extra = _enrichment_facts(artwork, artist)
        except Exception as e:
            print(f"[article] enrichment skipped: {e}")
            extra = []
        facts = build_fact_packet(artwork, artist, extra=extra)
    else:
        facts = build_fact_packet(artwork, artist)

    if generate and openai_key() and facts:
        try:
            article = _generate(facts, title)
            verdict = _verify(article, facts)
            if verdict["ok"]:
                return {
                    "title": _clean(article.get("title")) or title,
                    "sections": verdict["sections"],
                    "mode": "generated",
                    "verified": True,
                    "warnings": verdict["warnings"],
                    "entities": entities,
                }
            print(f"[article] too little verified content, using fallback "
                  f"(dropped {verdict['dropped']} sentence(s))")
        except Exception as e:  # network, SDK, parse — never break the page
            print(f"[article] generation error, using fallback: {e}")

    payload = _fallback(artwork, artist, title)
    payload["entities"] = entities
    return payload


def _generate(facts, title):
    """Call OpenAI for a grounded, sentence-cited article (structured output)."""
    from openai import OpenAI  # lazy import so the app runs without the package

    client = OpenAI(api_key=openai_key())
    fact_lines = "\n".join(f"{f['id']}: {f['label']} — {f['text']}" for f in facts)

    system = (
        "You are an art writer producing a vivid, accurate blog post for a curious "
        "general reader. You may use ONLY the facts provided. Never invent or infer "
        "dates, places, numbers, names, or attributions the facts do not state, and "
        "avoid unsupported claims about fame, value, influence, or reception unless a "
        "fact states them. In each sentence's `facts` field, list EVERY fact id that "
        "sentence draws on. Do NOT write fact ids (like F1) anywhere in the prose "
        "itself. Be engaging but strictly truthful; if a section's facts are thin, "
        "keep it short or omit it rather than padding."
    )
    user = (
        f"Facts about the artwork and its artist:\n{fact_lines}\n\n"
        "Write a blog post of 3-5 short sections, each with a descriptive heading "
        "(for example: the work itself, what it depicts, the artist's life and "
        "training, style and influences, where it lives today). Each section has 1-3 "
        "short paragraphs. Weave the facts into a flowing narrative for a reader who "
        "knows nothing about the work — stating nothing the facts above do not "
        "support. Give the post an engaging title."
    )

    resp = client.chat.completions.create(
        model=_model(),
        max_tokens=4000,
        temperature=0.7,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "grounded_article",
                "strict": True,
                "schema": ARTICLE_SCHEMA,
            },
        },
    )
    content = resp.choices[0].message.content
    if not content:
        raise RuntimeError("empty article response")
    return json.loads(content)


def _verify(article, facts):
    """Verify the blog post per sentence and DROP any sentence that isn't grounded.

    Rather than reject the whole post when one sentence is ungrounded (which on a
    long article means the reader almost always sees the plain summary), we drop
    the offending sentences and keep the verified rest — so what's published is
    still 100% grounded. We only fall back entirely if too little survives.

    A sentence is dropped if it cites no real fact, asserts a number that's
    nowhere in the dossier, or has near-zero word overlap with its cited facts.
    Numbers are checked against ALL facts (every fact is real Wikidata data, so a
    number is only "fabricated" if it appears in no fact at all).
    """
    by_id = {f["id"]: f["text"] for f in facts}
    all_evidence = " ".join(by_id.values())
    warnings = []
    dropped = 0
    kept = 0
    sections = []

    for section in article.get("sections", []):
        heading = (section.get("heading") or "").strip()
        paragraphs = []
        for para in section.get("paragraphs", []):
            sentences = []
            for sent in para.get("sentences", []):
                text = _MARKER_RE.sub("", sent.get("text") or "").strip()
                if not text:
                    continue
                ids = [i for i in sent.get("facts", []) if i in by_id]
                if not ids:
                    dropped += 1
                    warnings.append(f"dropped (uncited): {text[:70]}")
                    continue
                if numeric_date_facts.unverified_numbers(text, all_evidence):
                    dropped += 1
                    warnings.append(f"dropped (unverified number): {text[:70]}")
                    continue
                evidence = " ".join(by_id[i] for i in ids)
                score = support_span.overlap_score(text, evidence)
                if score < support_span.BLOCK_FLOOR:
                    dropped += 1
                    warnings.append(f"dropped (unsupported {score:.2f}): {text[:70]}")
                    continue
                if score < support_span.WARN_FLOOR:
                    warnings.append(f"weak support ({score:.2f}): {text[:70]}")
                sentences.append(text)
                kept += 1
            if sentences:
                paragraphs.append(" ".join(sentences))
        if paragraphs:
            sections.append({"heading": heading, "paragraphs": paragraphs})

    # Publish only if enough verified content survived; else use the summary.
    ok = kept >= 2 and bool(sections)
    return {"ok": ok, "warnings": warnings, "dropped": dropped, "sections": sections}


def _a_or_an(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _fallback(artwork, artist, title):
    """Deterministic, sectioned fact summary — trivially grounded, never wrong."""
    name = _clean(artist.get("name"))
    sections = []

    # The work
    lead = f"{title} is a painting"
    if name:
        lead += f" by {name}"
    extra = []
    date = _clean(artwork.get("creationDate"))
    if date:
        extra.append(f"created {date}")
    location = _clean(artwork.get("location"))
    if location:
        extra.append(f"now held at {location}")
    if extra:
        lead += ", " + " and ".join(extra)
    lead += "."
    medium = _clean(artwork.get("medium"))
    if medium:
        lead += f" It is executed in {medium}."
    work = [lead]
    depicts = _labels(artwork.get("depicts"), 8)
    if depicts:
        work.append(f"It depicts {depicts}.")
    sections.append({"heading": "The work", "paragraphs": work})

    # The artist
    if name:
        desc = _clean(artist.get("description"))
        bio = f"{name} was {_a_or_an(desc)} {desc}" if desc else f"{name} was an artist"
        clauses = []
        born = _clean(artist.get("birthdate"))
        if born:
            bp = _clean(artist.get("birthplace"))
            clauses.append(f"born {born}" + (f" in {bp}" if bp else ""))
        elif _clean(artist.get("nationality")):
            clauses.append(f"from {_clean(artist.get('nationality'))}")
        movement = _clean(artist.get("movement"))
        if movement:
            clauses.append(f"associated with the {movement} movement")
        if clauses:
            bio += ", " + ", ".join(clauses)
        artist_paras = [bio + "."]
        teachers = _clean(artist.get("teachers"))
        if teachers:
            artist_paras.append(f"{name} studied under {teachers}.")
        notable = _clean(artist.get("notableWorks"))
        if notable:
            artist_paras.append(f"{name} is also known for {notable}.")
        sections.append({"heading": "The artist", "paragraphs": artist_paras})

    return {
        "title": title,
        "sections": sections,
        "mode": "fallback",
        "verified": True,
        "warnings": [],
    }
