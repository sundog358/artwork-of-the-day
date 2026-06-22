"""Linked Art (https://linked.art/api/1.0/) serialization.

Maps our Wikidata dossier to Linked Art JSON-LD records — a CIDOC-CRM profile
used across the cultural-heritage Linked Open Data community. Produces five
dereferenceable record types, cross-linked by `id` and tied to Wikidata via
`equivalent`:

  - HumanMadeObject (artwork)  → object_record   → /object/<QID>
  - VisualItem      (depicted) → visual_record   → /visual/<QID>
  - Person          (artist)   → person_record   → /person/<QID>
  - Place           (location) → place_record    → /place/<QID>
  - Group           (owner)    → group_record    → /group/<QID>

Every record here is the **semantic** document and validates against the
official Linked Art JSON Schemas (linked-art/json-validator). The HAL `_links`
envelope the API spec describes is non-semantic and is attached at the response
layer (see app.py `_with_hal`), not here — the schemas set
`additionalProperties: false`, so `_links` deliberately lives outside the
validated body.

Vocabulary: Getty AAT for classifications/units/materials; Wikidata (and Getty
TGN/ULAN where available) entity URIs for `equivalent` and cross-references.
References point at our own /place, /group, /person, /visual endpoints so a
consumer can follow the graph; each of those carries the Wikidata `equivalent`.
"""

CONTEXT = "https://linked.art/ns/v1/linked-art.json"
_AAT = "http://vocab.getty.edu/aat/"


def _clean(v):
    return (
        ""
        if (
            not v
            or v in ("Unknown", "Untitled", "Unknown artist", "Unknown place", "Unknown group")
        )
        else str(v)
    )


def _wd(qid):
    return f"http://www.wikidata.org/entity/{qid}" if qid else None


# --- value objects ---------------------------------------------------------- #


def _concept(uri, label, meta=None):
    """A Concept/Type reference (classified_as item): id + type 'Type'."""
    d = {"id": uri, "type": "Type", "_label": label}
    if meta:
        d["classified_as"] = [meta]
    return d


def _aat(num, label, meta=None):
    return _concept(_AAT + num, label, meta)


def _lang_en():
    # LanguageRef forbids extra keys (no "notation").
    return {"id": _AAT + "300388277", "type": "Language", "_label": "English"}


def _name(content):
    return {
        "type": "Name",
        "content": content,
        "language": [_lang_en()],
        "classified_as": [_aat("300404670", "Primary Name")],
    }


def _identifier(content, aat_num, label):
    return {"type": "Identifier", "content": content, "classified_as": [_aat(aat_num, label)]}


def _description(text):
    return {
        "type": "LinguisticObject",
        "content": text,
        "classified_as": [_aat("300435416", "Description")],
    }


def _timespan(raw_iso, display):
    if not raw_iso:
        return None
    ts = {"type": "TimeSpan", "begin_of_the_begin": raw_iso, "end_of_the_end": raw_iso}
    disp = _clean(display)
    if disp:
        ts["identified_by"] = [
            {
                "type": "Name",
                "content": disp,
                "classified_as": [_aat("300404669", "Display Title")],
            }
        ]
    return ts


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _dimension(value, aat_num, label):
    return {
        "type": "Dimension",
        "classified_as": [_aat(aat_num, label)],
        "value": value,
        "unit": {"id": _AAT + "300379098", "type": "MeasurementUnit", "_label": "centimeters"},
    }


# Common mediums → AAT material terms. Unknown mediums fall back to their
# Wikidata entity URI (still a valid Material reference), so coverage is total
# whenever the medium has a QID.
_MATERIALS = {
    "oil paint": ("300015050", "oil paint"),
    "oil painting": ("300015050", "oil paint"),
    "watercolor paint": ("300015045", "watercolor"),
    "watercolour paint": ("300015045", "watercolor"),
    "watercolor": ("300015045", "watercolor"),
    "watercolour": ("300015045", "watercolor"),
    "tempera": ("300015062", "tempera"),
    "acrylic paint": ("300015058", "acrylic paint"),
    "gouache": ("300070114", "gouache"),
    "ink": ("300015012", "ink"),
    "canvas": ("300014078", "canvas"),
    "chalk": ("300011727", "chalk"),
    "charcoal": ("300022414", "charcoal"),
    "pastel": ("300077772", "pastel"),
    "fresco": ("300177433", "fresco"),
    "bronze": ("300010957", "bronze"),
    "marble": ("300011443", "marble"),
    "panel": ("300014657", "panel"),
}


def _material(medium_label, medium_qid):
    label = _clean(medium_label)
    key = label.lower()
    hit = _MATERIALS.get(key)
    if hit:
        num, lbl = hit
        return {"id": _AAT + num, "type": "Material", "_label": lbl}
    if medium_qid:  # any medium with a QID still maps
        return {"id": _wd(medium_qid), "type": "Material", "_label": label or "material"}
    return None


# --- entity references (all require an id per the schema) -------------------- #


def _place_ref(base, qid, label):
    return (
        {"id": f"{base}/place/{qid}", "type": "Place", "_label": _clean(label) or qid}
        if qid
        else None
    )


def _group_ref(base, qid, label):
    return (
        {"id": f"{base}/group/{qid}", "type": "Group", "_label": _clean(label) or qid}
        if qid
        else None
    )


def _wd_equivalent(qid, type_):
    return {"id": _wd(qid), "type": type_}


# --------------------------------------------------------------------------- #
# HumanMadeObject                                                              #
# --------------------------------------------------------------------------- #
def object_record(
    artwork,
    artist,
    *,
    base,
    object_uri,
    person_uri,
    artwork_qid,
    artist_qid,
    description="",
    entity_types=None,
):
    title = artwork.get("title") or "Untitled"
    classified = [
        _aat("300033618", "Painting", meta=_aat("300435443", "Type of Work")),
        _aat("300133025", "Artwork"),
    ]
    if artwork.get("genreQid"):
        classified.append(
            _concept(_wd(artwork["genreQid"]), _clean(artwork.get("genre")) or "genre")
        )

    rec = {
        "@context": CONTEXT,
        "id": object_uri,
        "type": "HumanMadeObject",
        "_label": title,
        "classified_as": classified,
        "identified_by": [_name(title)],
    }
    inv = _clean(artwork.get("inventory"))
    if inv:
        rec["identified_by"].append(_identifier(inv, "300312355", "Accession Number"))
    if _clean(description):
        rec["referred_to_by"] = [_description(description)]

    dims = []
    h, w = _num(artwork.get("heightCm")), _num(artwork.get("widthCm"))
    if h:
        dims.append(_dimension(h, "300055644", "Height"))
    if w:
        dims.append(_dimension(w, "300055647", "Width"))
    if dims:
        rec["dimension"] = dims

    mat = _material(artwork.get("medium"), artwork.get("mediumQid"))
    if mat:
        rec["made_of"] = [mat]

    prod = {"type": "Production"}
    if person_uri and artist and _clean(artist.get("name")):
        prod["carried_out_by"] = [{"id": person_uri, "type": "Person", "_label": artist["name"]}]
    cp = _place_ref(base, artwork.get("creationPlaceQid"), artwork.get("creationPlace"))
    if cp:
        prod["took_place_at"] = [cp]
    ts = _timespan(artwork.get("creationDateRaw"), artwork.get("creationDate"))
    if ts:
        prod["timespan"] = ts
    if len(prod) > 1:
        rec["produced_by"] = prod

    loc = _place_ref(base, artwork.get("locationQid"), artwork.get("location"))
    if loc:
        rec["current_location"] = loc
    coll = artwork.get("collection") or []
    if coll and coll[0].get("qid"):
        owner = _group_ref(base, coll[0]["qid"], coll[0].get("label"))
        if owner:
            rec["current_owner"] = [owner]

    if artwork.get("depicts"):
        rec["shows"] = [
            {
                "id": f"{base}/visual/{artwork_qid}",
                "type": "VisualItem",
                "_label": f"Visual content of {title}",
            }
        ]

    image = _clean(artwork.get("image"))
    if image:
        rec["representation"] = [
            {
                "type": "VisualItem",
                "_label": f"Digital image of {title}",
                "digitally_shown_by": [
                    {
                        "type": "DigitalObject",
                        "_label": "Image file",
                        "format": "image/jpeg",
                        "access_point": [{"id": image, "type": "DigitalObject"}],
                    }
                ],
            }
        ]

    rec["equivalent"] = [{"id": _wd(artwork_qid), "type": "HumanMadeObject", "_label": title}]
    return rec


# --------------------------------------------------------------------------- #
# VisualItem — what the object shows (depicted subjects)                       #
# --------------------------------------------------------------------------- #
def visual_record(artwork, *, visual_uri, artwork_qid, entity_types=None):
    title = artwork.get("title") or "Untitled"
    entity_types = entity_types or {}
    represents = []
    for d in (artwork.get("depicts") or [])[:15]:
        q = d.get("qid")
        if not q:
            continue
        represents.append(
            {
                "id": _wd(q),
                "type": entity_types.get(q, "Type"),
                "_label": d.get("label") or q,
            }
        )
    rec = {
        "@context": CONTEXT,
        "id": visual_uri,
        "type": "VisualItem",
        "_label": f"Visual content of {title}",
        "classified_as": [_aat("300033618", "Painting", meta=_aat("300435443", "Type of Work"))],
    }
    if represents:
        rec["represents"] = represents
    rec["equivalent"] = [{"id": _wd(artwork_qid), "type": "VisualItem", "_label": title}]
    return rec


# --------------------------------------------------------------------------- #
# Person                                                                       #
# --------------------------------------------------------------------------- #
def person_record(artist, *, base, person_uri, artist_qid, description=""):
    name = artist.get("name") or "Unknown"
    rec = {
        "@context": CONTEXT,
        "id": person_uri,
        "type": "Person",
        "_label": name,
        "identified_by": [_name(name)],
    }

    b_ts = _timespan(artist.get("birthDateRaw"), artist.get("birthdate"))
    b_place = _place_ref(base, artist.get("birthPlaceQid"), artist.get("birthplace"))
    if b_ts or b_place:
        born = {"type": "Birth"}
        if b_ts:
            born["timespan"] = b_ts
        if b_place:
            born["took_place_at"] = [b_place]
        rec["born"] = born

    d_ts = _timespan(artist.get("deathDateRaw"), artist.get("deathdate"))
    d_place = _place_ref(base, artist.get("deathPlaceQid"), artist.get("deathplace"))
    if d_ts or d_place:
        died = {"type": "Death"}
        if d_ts:
            died["timespan"] = d_ts
        if d_place:
            died["took_place_at"] = [d_place]
        rec["died"] = died

    desc = _clean(description) or _clean(artist.get("description"))
    if desc:
        rec["referred_to_by"] = [_description(desc)]

    rec["equivalent"] = [{"id": _wd(artist_qid), "type": "Person", "_label": name}]
    return rec


# --------------------------------------------------------------------------- #
# Place                                                                        #
# --------------------------------------------------------------------------- #
def place_record(place, *, place_uri, place_qid):
    name = place.get("name") or "Unknown place"
    rec = {
        "@context": CONTEXT,
        "id": place_uri,
        "type": "Place",
        "_label": name,
        "identified_by": [_name(name)],
    }
    desc = _clean(place.get("description"))
    if desc:
        rec["referred_to_by"] = [_description(desc)]
    wkt = _clean(place.get("wkt"))
    if wkt:
        rec["defined_by"] = wkt
    equivalent = [{"id": _wd(place_qid), "type": "Place", "_label": name}]
    if _clean(place.get("tgn")):
        equivalent.append({"id": f"http://vocab.getty.edu/tgn/{place['tgn']}", "type": "Place"})
    rec["equivalent"] = equivalent
    return rec


# --------------------------------------------------------------------------- #
# Group                                                                        #
# --------------------------------------------------------------------------- #
def group_record(group, *, group_uri, group_qid):
    name = group.get("name") or "Unknown group"
    rec = {
        "@context": CONTEXT,
        "id": group_uri,
        "type": "Group",
        "_label": name,
        "identified_by": [_name(name)],
    }
    desc = _clean(group.get("description"))
    if desc:
        rec["referred_to_by"] = [_description(desc)]
    inception = _clean(group.get("inception"))
    if inception:
        ts = _timespan(inception, inception[:4])
        if ts:
            rec["formed_by"] = {"type": "Formation", "timespan": ts}
    equivalent = [{"id": _wd(group_qid), "type": "Group", "_label": name}]
    if _clean(group.get("ulan")):
        equivalent.append({"id": f"http://vocab.getty.edu/ulan/{group['ulan']}", "type": "Group"})
    rec["equivalent"] = equivalent
    return rec
