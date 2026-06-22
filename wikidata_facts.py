"""Generic Wikidata narration library — NOT artwork-specific.

The idea: separate *fetching* the graph (schema-agnostic) from *narrating* it
(a curated, extensible registry). `statements_for(qids)` asks any entity "what
statements do you have?" and Wikidata answers; `narrate(stmts)` turns the ones
worth saying into sentences. The same code enriches an artwork, an artist, a
museum, a place, or a depicted subject — every entity is narratable by one engine.

Quality control:
  - external-identifier properties (VIAF, Freebase, catalog IDs…) are dropped at
    the query, via their Wikidata datatype — no denylist needed for those;
  - REGISTRY maps high-value PIDs to polished sentence templates;
  - DENY hides the remaining Wikimedia-meta noise (name variants, focus lists…);
  - everything else non-ID renders with a safe generic fallback ("Its {prop}: {v}").
Add a fact type = add one REGISTRY line. As Wikidata grows, the prose grows.
"""

import re

import sparql_library as S

QID_RE = S.QID_RE


def _and_list(labels):
    labels = [l for l in labels if l]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


# Curated PID -> (template, priority). Lower priority sorts earlier. {v} = the
# joined value label(s). Templates are phrased so the PID's natural entity-type
# fits (P186 only occurs on works, P166 only on people, etc.).
REGISTRY = {
    # --- artwork: the work itself ---
    "P186": ("Painted in {v}", 10),  # made from material
    "P88": ("Commissioned by {v}", 12),  # commissioned by
    "P1071": ("Made in {v}", 14),  # location of creation
    "P495": ("Originating from {v}", 16),  # country of origin
    "P179": ("Part of the series {v}", 18),  # part of the series
    "P144": ("Based on {v}", 20),  # based on
    "P941": ("Inspired by {v}", 21),  # inspired by
    "P921": ("Its main subject is {v}", 22),  # main subject
    "P135": ("In the {v} style", 24),  # movement
    # --- artwork: history & provenance ---
    "P793": ("It has witnessed {v}", 30),  # significant event (theft, vandalism…)
    "P608": ("Shown in exhibitions including {v}", 32),  # exhibition history
    "P127": ("Owned by {v}", 34),  # owned by
    "P6216": ("Its copyright status is {v}", 40),  # copyright status
    "P1268": ("Represents {v}", 41),  # represents
    # --- person: the artist ---
    "P166": ("Awarded {v}", 50),  # award received
    "P463": ("A member of {v}", 52),  # member of
    "P39": ("Held the position of {v}", 54),  # position held
    "P101": ("Working in the field of {v}", 56),  # field of work
    "P800": ("Noted for {v}", 58),  # notable work
    "P937": ("Active in {v}", 60),  # work location
    "P551": ("Resident in {v}", 62),  # residence
    "P1344": ("A participant in {v}", 63),  # participant in
    "P140": ("Worldview: {v}", 64),  # religion or worldview
    "P1412": ("Spoke {v}", 66),  # languages spoken/written
    "P53": ("Of the {v} family", 67),  # family
    # --- place / organisation ---
    "P1376": ("The capital of {v}", 70),  # capital of
    "P159": ("Headquartered in {v}", 72),  # headquarters location
    "P138": ("Named after {v}", 74),  # named after
    "P112": ("Founded by {v}", 76),  # founded by
    "P131": ("Located in {v}", 90),  # admin territorial entity
}

# Wikimedia-meta / redundant noise to hide (the external IDs are already gone).
DENY = {
    "P31",  # instance of (we already frame the type)
    "P528",  # catalog code
    "P217",  # inventory number (shown elsewhere)
    "P1545",  # series ordinal
    "P2561",
    "P1448",
    "P1449",
    "P1813",
    "P1705",  # name / official name / nickname / short name
    "P460",
    "P1889",  # said to be the same as / different from
    "P5008",
    "P6104",  # on focus list of / maintained by WikiProject
    "P21",  # sex or gender (not relevant context here)
    "P1424",
    "P910",
    "P1151",
    "P935",
    "P1472",
    "P1612",  # topic's main template/category/portal/Commons pages
    "P3342",  # significant person (often noise on places/orgs)
    "P18",
    "P94",
    "P158",
    "P237",
    "P163",  # image / coat of arms / seal / flag (media-ish)
    "P373",  # Commons category (handled as a link)
    "P1687",
    "P1659",
    "P1855",  # Wikidata-property meta
    "P1830",
    "P527",
    "P2670",  # owner of / has part(s) (verbose, not about this entity)
    "P1343",  # described by source (mostly old-encyclopedia cruft)
    "P1476",
    "P735",
    "P734",
    "P742",  # title / given name / family name / pseudonym (noise)
    "P6886",  # writing language (redundant with languages spoken)
    "P509",
    "P1196",
    "P157",
    "P1347",  # cause/manner of death, killed by (morbid, not artistic)
    "P6379",  # has works in the collection (we cover collections elsewhere)
    "P170",
    "P276",
    "P195",
    "P180",
    "P571",
    "P136",  # core facts already in the base article
    "P2048",
    "P2049",
    "P2067",
    "P1083",  # raw dimensions/mass/capacity (no unit here)
}


def statements_for(qids):
    """Generic, batched: every truthy statement of each entity (external-IDs
    excluded via datatype), with property + value labels. Returns
    {qid: [{pid, prop, value, vqid}]}."""
    valid = [q for q in dict.fromkeys(qids) if QID_RE.match(q)]
    out = {q: [] for q in valid}
    if not valid:
        return out
    values = " ".join(f"wd:{q}" for q in valid)
    query = (
        """
    SELECT ?subj ?prop ?propLabel ?v ?vLabel WHERE {
      VALUES ?subj { %s }
      ?subj ?pd ?v.
      ?prop wikibase:directClaim ?pd; wikibase:propertyType ?pt.
      FILTER(?pt != wikibase:ExternalId)
      ?prop rdfs:label ?propLabel. FILTER(LANG(?propLabel) = "en")
      OPTIONAL { ?v rdfs:label ?vl. FILTER(LANG(?vl) = "en") }
      BIND(COALESCE(?vl, STR(?v)) AS ?vLabel)
    }
    """
        % values
    )
    try:
        rows = S.run_sparql(query, timeout=40)
    except Exception as e:
        print(f"statements_for error: {e}")
        return out
    for r in rows:
        subj = S.qid(r.get("subj", {}).get("value", ""))
        if subj not in out:
            continue
        prop_uri = r.get("prop", {}).get("value", "")
        pid = prop_uri.rsplit("/", 1)[-1]
        vraw = r.get("v", {}).get("value", "")
        out[subj].append(
            {
                "pid": pid,
                "prop": r.get("propLabel", {}).get("value", ""),
                "value": r.get("vLabel", {}).get("value", ""),
                "vqid": S.qid(vraw) if "/entity/Q" in vraw else "",
            }
        )
    return out


_DATE_RE = re.compile(r"^[+-]?\d{3,4}-\d{2}-\d{2}T")
_NUM_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")


def _fmt(s):
    """Format one statement's value; '' = drop it (bare number / URL)."""
    v = s["value"]
    if not v or v.startswith("http"):
        return ""
    if _DATE_RE.match(v):
        return S.format_date(v)
    if _NUM_RE.match(v) and not s["vqid"]:
        return ""  # unitless number — meaningless in prose
    return v


def narrate(stmts, skip=(), limit=10):
    """Turn an entity's statements into ordered sentences. Registry PIDs get
    polished templates; other non-ID, non-deny PIDs get a safe generic fallback.
    Multi-valued props collapse into one sentence. Returns [sentence]."""
    skip = set(skip)
    by_pid = {}
    for s in stmts:
        pid = s["pid"]
        if pid in DENY or pid in skip:
            continue
        by_pid.setdefault(pid, []).append(s)

    items = []  # (priority, sentence)
    for pid, group in by_pid.items():
        vals = []
        for s in group:
            f = _fmt(s)
            if f and f not in vals:
                vals.append(f)
        if not vals:
            continue
        joined = _and_list(vals[:5])
        if pid in REGISTRY:
            tmpl, prio = REGISTRY[pid]
            items.append((prio, tmpl.format(v=joined) + "."))
        else:
            prop = group[0]["prop"]
            # Capitalise the property label for the lead of a fallback sentence.
            label = prop[:1].upper() + prop[1:] if prop else "Also"
            items.append((500, f"{label}: {joined}."))
    items.sort(key=lambda x: x[0])
    return [s for _, s in items[:limit]]
