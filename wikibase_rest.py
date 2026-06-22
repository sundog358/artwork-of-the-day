"""Thin client for the Wikibase REST API (Wikidata).

A deliberate hybrid with sparql_library: this module does *entity-by-id* reads on
the MediaWiki API backend (NOT the WDQS/SPARQL endpoint), which is where qualifier
data — start/end dates on statements — comes back cleanly, and which takes load
OFF the throttled SPARQL service. All graph work (search, traversal, ranking,
aggregation) stays on SPARQL, because this API can't do it.

Used for the dated-facts layer: significant events with their span ("stolen in
1911, recovered in 1913") and awards with the year received.
"""
import os
import re

import requests

_BASE = "https://www.wikidata.org/w/rest.php/wikibase/v1"
_CONTACT = os.environ.get("AOTD_CONTACT", "https://metahistorybook.com")
_UA = {"User-Agent": f"ArtworkOfTheDay/1.0 ({_CONTACT})", "Accept": "application/json"}
_QID = re.compile(r"^Q\d+$")
_YEAR = re.compile(r"[+-]?(\d{4})")

# Qualifier PIDs that carry a date: point in time / start time, and end time.
_START_QUALS = ("P585", "P580")
_END_QUAL = "P582"


def _get(path, params=None):
    """GET a REST path; None on any failure (caller degrades gracefully)."""
    try:
        r = requests.get(f"{_BASE}{path}", headers=_UA, params=params, timeout=15)
    except Exception as e:
        print(f"wikibase_rest GET {path} failed: {e}")
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _year(content):
    """Pull the 4-digit year from a time qualifier value ({'time': '+1911-…'})."""
    if isinstance(content, dict):
        m = _YEAR.search(content.get("time", ""))
        return m.group(1) if m else ""
    return ""


def dated_statements(qid, pid):
    """An entity's `pid` statements with their date span, via the REST API.
    Returns [{qid, start, end}] — start from P585/P580, end from P582 — so the
    caller can say 'stolen 1911, recovered 1913'."""
    if not _QID.match(qid):
        return []
    data = _get(f"/entities/items/{qid}/statements", {"property": pid})
    if not data:
        return []
    out = []
    for st in data.get(pid, []):
        val = st.get("value", {})
        if val.get("type") != "value":
            continue
        vqid = val.get("content")
        if not isinstance(vqid, str) or not _QID.match(vqid):
            continue
        start = end = ""
        for q in st.get("qualifiers", []):
            qp = (q.get("property") or {}).get("id")
            qv = (q.get("value") or {}).get("content")
            if qp in _START_QUALS and not start:
                start = _year(qv)
            elif qp == _END_QUAL:
                end = _year(qv)
        out.append({"qid": vqid, "start": start, "end": end})
    return out


def labels(qids):
    """{qid: en label} via the REST /labels endpoint (entity-by-id, off-WDQS).
    Sequential by design — callers run this inside one parallel enrichment task,
    and there are only a handful of values to resolve."""
    out = {}
    for q in dict.fromkeys(qids):
        if not _QID.match(q):
            continue
        d = _get(f"/entities/items/{q}/labels")
        if d and d.get("en"):
            out[q] = d["en"]
    return out


def dated_facts(qid, pid):
    """End-to-end via the REST API: statements+qualifiers, then resolve the value
    labels. Returns [(label, start, end)] sorted by start year (dated first).
    Fully off the SPARQL endpoint."""
    stmts = dated_statements(qid, pid)
    if not stmts:
        return []
    lbls = labels([s["qid"] for s in stmts])
    out, seen = [], set()
    for s in stmts:
        lbl = lbls.get(s["qid"])
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append((lbl, s["start"], s["end"]))
    out.sort(key=lambda x: (x[1] == "", x[1]))
    return out


def reference_sources(qid, limit=5):
    """The data's PROVENANCE: distinct 'stated in' (P248) sources cited across an
    entity's statement references, ranked by how often they're cited. Returns
    [label]. The REST API exposes references natively, which SPARQL cannot do
    cleanly. One statements call + a few label lookups, off WDQS."""
    if not _QID.match(qid):
        return []
    data = _get(f"/entities/items/{qid}/statements")
    if not data:
        return []
    counts = {}
    for stmts in data.values():
        for s in stmts:
            for ref in s.get("references", []):
                for part in ref.get("parts", []):
                    if (part.get("property") or {}).get("id") == "P248":
                        v = (part.get("value") or {}).get("content")
                        if isinstance(v, str) and _QID.match(v):
                            counts[v] = counts.get(v, 0) + 1
    ranked = sorted(counts, key=lambda q: -counts[q])
    lbls = labels(ranked[: limit + 4])
    # Drop import-artefact "sources" that aren't real scholarship.
    noise = ("freebase", "data dump", "wikimedia", "wikidata", "import")
    out = []
    for q in ranked:
        lbl = lbls.get(q)
        if lbl and not any(n in lbl.lower() for n in noise):
            out.append(lbl)
        if len(out) >= limit:
            break
    return out
