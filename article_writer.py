"""Deterministic article builder for Artwork of the Day.

No model, no API key, no per-token cost: the "About" section is assembled
directly from the Wikidata dossier that `sparql_library.build_dossier` already
gathers (artwork facts, full artist bio, one-hop neighbourhoods WITH short
descriptions, contemporaries). Because every sentence is composed from real
Wikidata values, the result is trivially grounded — it can never state anything
the data doesn't support.

`build(artwork, artist)` returns a sectioned payload the frontend renders:
    {title, sections: [{heading, paragraphs: [str, ...]}], mode, entities}
"""


def _clean(value):
    """Treat our placeholder values as missing."""
    if not value or value in ("Unknown", "Untitled", "Unknown artist", ""):
        return ""
    return str(value).strip()


def _a_or_an(word):
    return "an" if word[:1].lower() in "aeiou" else "a"


def _labels(items, limit=8):
    """Comma-join the labels of a [{qid,label}] neighbourhood list."""
    labels = [d.get("label", "") for d in (items or []) if d.get("label")]
    return ", ".join(labels[:limit])


def _and_list(labels):
    """Join a list of phrases as 'a', 'a and b', or 'a, b, and c'."""
    labels = [l for l in labels if l]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _gloss(items, limit=3):
    """Take named entities that carry a Wikidata description and render them as
    'Label (description)' appositives — e.g. 'Florence (city in Italy)'. These
    one-liners are what let the prose explain *who/what* something is, not just
    name it. Generic concepts (lowercase labels like 'sky') are skipped."""
    out = []
    for d in items or []:
        label = (d.get("label") or "").strip()
        desc = (d.get("description") or "").strip()
        if not label or not any(c.isupper() for c in label):
            continue
        out.append(f"{label} ({desc})" if desc else label)
        if len(out) >= limit:
            break
    return out


def build(artwork, artist, generate=False):
    """Return the sectioned 'About' payload. Always succeeds; always grounded.

    `generate` is accepted for backward compatibility with older callers and is
    ignored — generation is always the deterministic Wikidata assembly now.
    """
    title = _clean(artwork.get("title")) or "This artwork"
    name = _clean(artist.get("name"))
    sections = []

    # --- The painting ------------------------------------------------------ #
    lead = f"{title} is a painting"
    if name:
        lead += f" by {name}"
    date = _clean(artwork.get("creationDate"))
    place = _clean(artwork.get("creationPlace"))
    country = _clean(artwork.get("country"))
    where_made = place or country
    tail = []
    if date:
        tail.append(f"created in {date}")
    if where_made:
        tail.append(f"made in {where_made}")
    if tail:
        lead += ", " + " and ".join(tail)
    lead += "."
    work = [lead]

    extra = []
    medium = _clean(artwork.get("medium"))
    if medium:
        extra.append(f"is executed in {medium}")
    dims = _clean(artwork.get("dimensions"))
    if dims:
        extra.append(f"measures {dims}")
    genre = _clean(artwork.get("genre"))
    if genre:
        extra.append(f"belongs to the genre of {genre}")
    if extra:
        work.append("It " + _and_list(extra) + ".")

    provenance = []
    commissioned = _clean(artwork.get("commissionedBy"))
    if commissioned:
        provenance.append(f"was commissioned by {commissioned}")
    series = _clean(artwork.get("series"))
    if series:
        provenance.append(f"forms part of the series {series}")
    inventory = _clean(artwork.get("inventory"))
    if inventory:
        provenance.append(f"is catalogued under inventory number {inventory}")
    if provenance:
        work.append("It " + _and_list(provenance) + ".")
    sections.append({"heading": "The painting", "paragraphs": work})

    # --- What it depicts --------------------------------------------------- #
    depicts = artwork.get("depicts") or []
    main_subject = _clean(artwork.get("mainSubject"))
    depict_paras = []
    all_labels = _labels(depicts, 10)
    label_set = {(d.get("label") or "").lower() for d in depicts}
    if all_labels:
        depict_paras.append(f"The painting depicts {all_labels}.")
    glossed = _gloss(depicts, 3)
    if glossed:
        verb = "is" if len(glossed) == 1 else "are"
        depict_paras.append(f"Among them {verb} " + _and_list(glossed) + ".")
    if main_subject and main_subject.lower() not in label_set:
        depict_paras.append(f"Its main subject is {main_subject}.")
    if depict_paras:
        sections.append({"heading": "What it depicts", "paragraphs": depict_paras})

    # --- The artist -------------------------------------------------------- #
    if name:
        desc = _clean(artist.get("description"))
        bio = f"{name} was {_a_or_an(desc)} {desc}" if desc else f"{name} was an artist"
        clauses = []
        born = _clean(artist.get("birthdate"))
        if born:
            bp = _clean(artist.get("birthplace"))
            clauses.append(f"born {born}" + (f" in {bp}" if bp else ""))
        died = _clean(artist.get("deathdate"))
        if died:
            dp = _clean(artist.get("deathplace"))
            clauses.append(f"died {died}" + (f" in {dp}" if dp else ""))
        nationality = _clean(artist.get("nationality"))
        if nationality and not desc:
            clauses.append(f"of {nationality} nationality")
        if clauses:
            bio += ", " + ", ".join(clauses)
        artist_paras = [bio + "."]

        training = []
        education = _clean(artist.get("education"))
        if education:
            training.append(f"{name} trained at {education}")
        teachers = _clean(artist.get("teachers"))
        if teachers:
            training.append(("studied under " if training else f"{name} studied under ") + teachers)
        if training:
            artist_paras.append(_and_list(training) + ".")
        sections.append({"heading": "The artist", "paragraphs": artist_paras})

        # --- Style and influences ----------------------------------------- #
        style_paras = []
        movement = _clean(artist.get("movement"))
        genres = _clean(artist.get("genres"))
        sc = []
        if movement:
            sc.append(f"is associated with the {movement} movement")
        if genres:
            sc.append(f"worked in {genres}")
        if sc:
            style_paras.append(f"{name} " + _and_list(sc) + ".")
        influences = _gloss(artist.get("influencedBy"), 3)
        if influences:
            style_paras.append("Among the artist's influences: " + _and_list(influences) + ".")
        recognition = []
        notable = _clean(artist.get("notableWorks"))
        if notable:
            recognition.append(f"is also known for {notable}")
        awards = _clean(artist.get("awards"))
        if awards:
            recognition.append(f"received {awards}")
        memberships = _clean(artist.get("memberships"))
        if memberships:
            recognition.append(f"was a member of {memberships}")
        students = _clean(artist.get("students"))
        if students:
            recognition.append(f"taught {students}")
        if recognition:
            style_paras.append(f"{name} " + _and_list(recognition) + ".")
        if style_paras:
            sections.append({"heading": "Style and influences", "paragraphs": style_paras})

    # --- Where it lives today ---------------------------------------------- #
    location = _clean(artwork.get("location"))
    if location:
        home = [f"Today the painting is held at {location}."]
        # If the holding collection carries a Wikidata description, gloss it.
        for c in artwork.get("collection") or []:
            label, desc = _clean(c.get("label")), _clean(c.get("description"))
            if label and desc:
                home.append(f"{label} is {_a_or_an(desc)} {desc}.")
                break
        sections.append({"heading": "Where it lives today", "paragraphs": home})

    # --- Contemporaries ---------------------------------------------------- #
    contemporaries = artist.get("contemporaries") or []
    peers = _gloss(contemporaries, 4) or (
        [_labels(contemporaries, 4)] if _labels(contemporaries, 4) else []
    )
    if peers and name:
        sections.append(
            {
                "heading": "In context",
                "paragraphs": [
                    f"{name} worked alongside contemporaries such as " + _and_list(peers) + "."
                ],
            }
        )

    return {
        "title": title,
        "sections": sections,
        "mode": "wikidata",
        "entities": artwork.get("_link_entities", []),
    }
