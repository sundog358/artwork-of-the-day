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

from media_types import image_format

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


# Authority-file id → canonical URI. Each becomes an `equivalent` reference so a
# consumer can pivot from our record to VIAF / Getty / ISNI / GeoNames / RKD.
_AUTHORITY = {
    "viaf": ("https://viaf.org/viaf/{}", None),
    "ulan": ("http://vocab.getty.edu/ulan/{}", None),
    "tgn": ("http://vocab.getty.edu/tgn/{}", None),
    "rkd": ("https://rkd.nl/en/explore/artists/{}", None),
    "isni": ("https://isni.org/isni/{}", lambda v: v.replace(" ", "")),
    "geonames": ("https://sws.geonames.org/{}/", None),
}


def _equivalents(qid, type_, label, facts, keys):
    """[Wikidata entity] + every present authority id, as `equivalent` refs."""
    out = [{"id": _wd(qid), "type": type_, "_label": label}]
    for k in keys:
        v = _clean(facts.get(k))
        if not v:
            continue
        tmpl, fn = _AUTHORITY[k]
        out.append({"id": tmpl.format(fn(v) if fn else v), "type": type_})
    return out


def _year_span(start, end):
    """A TimeSpan from year strings (e.g. '1911', '1913'), with a display name."""
    s, e = _clean(start), _clean(end)
    if not s and not e:
        return None
    ts = {"type": "TimeSpan"}
    if s:
        ts["begin_of_the_begin"] = f"{s}-01-01T00:00:00Z" if len(s) == 4 else s
    if e:
        ts["end_of_the_end"] = f"{e}-12-31T23:59:59Z" if len(e) == 4 else e
    disp = f"{s}–{e}" if (s and e and s != e) else (s or e)
    ts["identified_by"] = [
        {"type": "Name", "content": disp, "classified_as": [_aat("300404669", "Display Title")]}
    ]
    return ts


def _activity(label, start, end, aat_num, aat_label):
    """A CIDOC-CRM Activity the object `used_for` (exhibition, significant event)."""
    act = {"type": "Activity", "_label": label, "classified_as": [_aat(aat_num, aat_label)]}
    span = _year_span(start, end)
    if span:
        act["timespan"] = span
    return act


# Linked Art `used_for` activity kinds we surface, with their Getty AAT type.
_ACTIVITY_AAT = {
    "exhibition": ("300054766", "Exhibition"),
    "event": ("300069103", "Event"),
}


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
            _concept(
                f"{base}/concept/{artwork['genreQid']}", _clean(artwork.get("genre")) or "genre"
            )
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
        cq = coll[0]["qid"]
        cl = _clean(coll[0].get("label")) or "a collection"
        owner = _group_ref(base, cq, coll[0].get("label"))
        if owner:
            rec["current_owner"] = [owner]
        # the object is a member of that institution's collection Set
        rec["member_of"] = [
            {"id": f"{base}/set/{cq}", "type": "Set", "_label": f"Collection of {cl}"}
        ]

    # Exhibitions and significant events the object was used for (CIDOC-CRM
    # Activities, dated from REST qualifiers upstream).
    used = []
    for a in artwork.get("activities") or []:
        num, lbl = _ACTIVITY_AAT.get(a.get("kind", "event"), _ACTIVITY_AAT["event"])
        used.append(_activity(a.get("label") or lbl, a.get("start"), a.get("end"), num, lbl))
    if used:
        rec["used_for"] = used

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
        shown_by = [
            {
                "type": "DigitalObject",
                "_label": "Image file",
                "format": image_format(None, image),
                "access_point": [{"id": image, "type": "DigitalObject"}],
            },
            {
                "type": "DigitalObject",
                "_label": f"IIIF Presentation manifest for {title}",
                "format": 'application/ld+json;profile="http://iiif.io/api/presentation/3/context.json"',
                "conforms_to": [
                    {"id": "http://iiif.io/api/presentation", "type": "InformationObject"}
                ],
                "access_point": [
                    {"id": f"{base}/iiif/{artwork_qid}/manifest.json", "type": "DigitalObject"}
                ],
            },
        ]
        rec["representation"] = [
            {
                "type": "VisualItem",
                "_label": f"Digital image of {title}",
                "digitally_shown_by": shown_by,
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

    rec["equivalent"] = _equivalents(
        artist_qid, "Person", name, artist, ["viaf", "ulan", "rkd", "isni"]
    )
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
    rec["equivalent"] = _equivalents(place_qid, "Place", name, place, ["tgn", "geonames"])
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
    rec["equivalent"] = _equivalents(group_qid, "Group", name, group, ["ulan", "viaf", "isni"])
    return rec


# --------------------------------------------------------------------------- #
# Concept (Type) — movements, genres, techniques                              #
# --------------------------------------------------------------------------- #
def concept_record(concept, *, concept_uri, concept_qid):
    name = concept.get("name") or "Concept"
    rec = {
        "@context": CONTEXT,
        "id": concept_uri,
        "type": "Type",
        "_label": name,
        "identified_by": [_name(name)],
    }
    desc = _clean(concept.get("description"))
    if desc:
        rec["referred_to_by"] = [_description(desc)]
    equivalent = [{"id": _wd(concept_qid), "type": "Type", "_label": name}]
    if _clean(concept.get("aat")):  # cross-walk to the Getty AAT vocabulary
        equivalent.append({"id": _AAT + concept["aat"], "type": "Type"})
    rec["equivalent"] = equivalent
    return rec


# --------------------------------------------------------------------------- #
# Set — a collection (the holdings of an institution)                          #
# --------------------------------------------------------------------------- #
def set_record(group, *, set_uri, set_qid):
    name = group.get("name") or "a collection"
    label = f"Collection of {name}"
    rec = {
        "@context": CONTEXT,
        "id": set_uri,
        "type": "Set",
        "_label": label,
        "identified_by": [_name(label)],
        "classified_as": [_aat("300025976", "Collection")],
    }
    desc = _clean(group.get("description"))
    body = f"The art collection of {name}." + (f" {desc}" if desc else "")
    rec["referred_to_by"] = [_description(body)]
    return rec
