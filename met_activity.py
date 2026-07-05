"""Meta Museum ActivityStreams consumer.

daily.metahistorybook.com consumes the **Meta Museum** activity stream
(``metamuseum.org/api/activity`` — our partner platform, a Linked Art
aggregator; not to be confused with the Metropolitan Museum) as a *distinct
external consumer*, identified as ``daily-metahistorybook-prod``. It walks the
ordered collection back from the most recent page, extracts newly-changed
paintings, and keeps a small persisted **candidate pool** that Daily draws a
supplementary artwork-of-the-day from — reconciling the stream against the app's
own Wikidata "artist born today" selection.

Meta Museum republishes collections from several data providers; we read the
``provider=met`` slice, whose objects are Metropolitan Museum records served
from ``collectionapi.metmuseum.org`` — hence the Metropolitan object ids parsed
below. Meta Museum (metamuseum.org) is the partner; "met" is just that provider
key.

The feed is IIIF Change Discovery (https://iiif.io/api/discovery/1.0/): an
``OrderedCollection`` whose ``first``..``last`` pages hold ``Create`` / ``Update``
/ ``Delete`` activities in oldest-to-newest order. Each activity's ``object`` is
a ``HumanMadeObject`` Linked Art record, dereferenceable at
``metamuseum.org/api/records/<uri>`` (wrapped in a ``{"record": {...}}`` envelope).

No API key: the feed is public. We send a descriptive ``User-Agent`` carrying
our consumer id and contact — the same courtesy convention the Wikidata clients
in this codebase use, and Meta Museum's evidence for attributing reads to Daily.

Reconciliation is cursor-based: the persisted state stores the timestamp of the
newest activity seen, and each refresh walks ``last -> prev`` newest-first,
stopping as soon as it reaches that cursor. So a refresh only ever processes
genuinely new changes.

Run a refresh from the CLI (prints a summary):

    python met_activity.py
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import requests

# --- Consumer identity ------------------------------------------------------ #
# The consumer id travels two ways on every outbound request: in the User-Agent
# (human-readable attribution) and in a dedicated `x-linked-art-consumer-id`
# header, which maps cleanly into Meta Museum's ActivityStreams readiness ledger.
CONSUMER_ID = os.environ.get("AOTD_MET_CONSUMER_ID", "daily-metahistorybook-prod")
_CONTACT = os.environ.get("AOTD_CONTACT", "https://daily.metahistorybook.com")
CONSUMER_ID_HEADER = "x-linked-art-consumer-id"
_HEADERS = {
    "User-Agent": f"{CONSUMER_ID} ({_CONTACT}; ActivityStreams consumer)",
    CONSUMER_ID_HEADER: CONSUMER_ID,
    "Accept": "application/activity+json, application/ld+json, application/json",
}

# Activity types Daily's callback accepts / applies to the pool.
ACCEPTED_EVENTS = ("Create", "Update", "Delete")
# Only ever dereference objects on Meta Museum's own host (SSRF guard for the
# public callback, which carries an attacker-controllable object id).
_ALLOWED_HOST = "metamuseum.org"

COLLECTION_URL = os.environ.get(
    "AOTD_MET_ACTIVITY_URL",
    "https://www.metamuseum.org/api/activity/collection?provider=met",
)
# Persisted candidate pool + reconciliation cursor. Runtime state; gitignored.
STATE_PATH = os.environ.get("AOTD_MET_STATE_PATH", "met_candidates.json")

# Bound the walk so an unexpectedly large feed can't spin forever.
MAX_PAGES = int(os.environ.get("AOTD_MET_MAX_PAGES", "40"))
# Cap dereferences per refresh (each new candidate costs one record fetch).
MAX_DEREF = int(os.environ.get("AOTD_MET_MAX_DEREF", "80"))
# Cap the persisted pool so it can't grow without bound (oldest-changed evicted).
MAX_POOL = int(os.environ.get("AOTD_MET_MAX_POOL", "500"))

_TIMEOUT = 20
_lock = threading.Lock()
_MET_ID_RE = re.compile(r"/objects/(\d+)")


def _get(url: str) -> dict[str, Any]:
    """GET a JSON document under the Daily consumer identity (both headers)."""
    r = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _is_met_url(url: str) -> bool:
    """True only for URLs on Meta Museum's host — the callback dereference guard."""
    host = (urlparse(url).hostname or "").lower()
    return host == _ALLOWED_HOST or host.endswith("." + _ALLOWED_HOST)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Feed walk -------------------------------------------------------------- #
def walk_back(since: str | None, max_pages: int = MAX_PAGES) -> Iterator[dict[str, Any]]:
    """Yield activities newest-first, walking pages from ``last`` via ``prev``.

    Stops once it reaches an activity at or before ``since`` (the stored
    reconciliation cursor), or after ``max_pages`` pages — whichever is first.
    Within a page ``orderedItems`` run oldest-to-newest, so we reverse them.
    """
    collection = _get(COLLECTION_URL)
    page_ref = collection.get("last") or collection.get("first")
    pages = 0
    while page_ref and pages < max_pages:
        page_id = page_ref["id"] if isinstance(page_ref, dict) else page_ref
        page = _get(page_id)
        pages += 1
        for act in reversed(page.get("orderedItems", [])):
            when = _activity_time(act)
            if since and when and when <= since:
                return
            yield act
        page_ref = page.get("prev")


def _activity_time(activity: dict[str, Any]) -> str:
    """The best available ISO timestamp for an activity (for cursor compares)."""
    return activity.get("endTime") or activity.get("published") or ""


# --- Record parsing --------------------------------------------------------- #
def _first_name(identified_by: list[dict[str, Any]]) -> str | None:
    for idb in identified_by:
        if idb.get("type") == "Name" and idb.get("content"):
            return idb["content"]
    return None


def parse_record(envelope: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields Daily needs from a Meta Museum Linked Art record.

    Accepts either the ``{"record": {...}}`` envelope Meta Museum serves or a
    bare record. Returns a flat candidate dict (image is best-effort: the served
    Linked Art often carries an empty ``representation``, so the stable
    Metropolitan object id is the real key for later reconciliation/thumbnailing).
    """
    rec = envelope.get("record", envelope)
    classes = [c.get("_label") for c in rec.get("classified_as", []) if c.get("_label")]

    artist: str | None = None
    artist_authority: str | None = None
    date: str | None = None
    prod = rec.get("produced_by") or {}
    actors = prod.get("carried_out_by") or []
    if actors:
        artist = actors[0].get("_label") or _first_name(actors[0].get("identified_by", []))
        # ULAN / authority URI when the record provides one.
        aid = actors[0].get("id") or ""
        if "ulan" in aid or "viaf" in aid or "wikidata" in aid:
            artist_authority = aid
    if isinstance(prod.get("timespan"), dict):
        date = prod["timespan"].get("_label")

    image: str | None = None
    for rep in rec.get("representation", []) or []:
        for ap in rep.get("access_point", []) or []:
            if ap.get("id"):
                image = ap["id"]
                break
        if image:
            break

    return {
        "title": _first_name(rec.get("identified_by", [])) or rec.get("_label"),
        "artist": artist,
        "artist_authority": artist_authority,
        "date": date,
        "classified_as": classes,
        "is_painting": any(c.lower() == "painting" for c in classes),
        "image": image,
    }


# --- Persisted state -------------------------------------------------------- #
def _blank_state() -> dict[str, Any]:
    return {
        "consumer_id": CONSUMER_ID,
        "source": COLLECTION_URL,
        "cursor": None,  # ISO time of the newest activity ever processed
        "updated_at": None,  # when we last ran a refresh
        "counts": {"pool": 0, "paintings": 0},
        "candidates": {},  # met_id -> candidate dict
    }


def load_state(path: str | None = None) -> dict[str, Any]:
    """Load the persisted candidate pool, or a blank one if none exists yet."""
    path = path or STATE_PATH
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _blank_state()
    # Fill in any keys added since the file was written.
    base = _blank_state()
    base.update(state)
    return base


def _save_state(state: dict[str, Any], path: str | None = None) -> None:
    path = path or STATE_PATH
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # atomic on POSIX and Windows


def _evict_to_cap(candidates: dict[str, Any]) -> dict[str, Any]:
    """Keep only the MAX_POOL most-recently-changed candidates."""
    if len(candidates) <= MAX_POOL:
        return candidates
    ordered = sorted(candidates.items(), key=lambda kv: kv[1].get("changed_at") or "", reverse=True)
    return dict(ordered[:MAX_POOL])


# --- Refresh (the actual consume) ------------------------------------------ #
def refresh(path: str | None = None, max_pages: int = MAX_PAGES) -> dict[str, Any]:
    """Consume new activity from the Meta Museum feed into the candidate pool.

    Walks newest-first from the stored cursor, dereferences each new ``Create``
    / ``Update`` object (bounded by ``MAX_DEREF``), adds paintings and other
    objects to the pool keyed by Metropolitan object id, and applies deletes.
    Returns a summary dict. Thread-safe.
    """
    path = path or STATE_PATH
    with _lock:
        state = load_state(path)
        cursor = state.get("cursor")
        candidates: dict[str, Any] = dict(state.get("candidates", {}))

        newest_seen = cursor
        processed = derefs = added = updated = removed = 0

        for act in walk_back(cursor, max_pages=max_pages):
            processed += 1
            when = _activity_time(act)
            if when and (newest_seen is None or when > newest_seen):
                newest_seen = when

            obj = act.get("object") or {}
            met_id = _met_id(obj.get("id", ""))
            if not met_id:
                continue
            action = (act.get("type") or "").lower()

            if action == "delete":
                if candidates.pop(met_id, None) is not None:
                    removed += 1
                continue

            if derefs >= MAX_DEREF:
                continue  # keep the cursor honest but stop fetching this run
            try:
                parsed = parse_record(_get(obj["id"]))
            except (requests.RequestException, ValueError, KeyError):
                continue
            derefs += 1

            is_new = met_id not in candidates
            candidates[met_id] = {
                "met_id": met_id,
                "record": obj["id"],
                "changed_at": when,
                "activity": act.get("type"),
                **parsed,
            }
            if is_new:
                added += 1
            else:
                updated += 1

        candidates = _evict_to_cap(candidates)
        paintings = sum(1 for c in candidates.values() if c.get("is_painting"))
        state.update(
            {
                "cursor": newest_seen,
                "updated_at": _now_iso(),
                "counts": {"pool": len(candidates), "paintings": paintings},
                "candidates": candidates,
            }
        )
        _save_state(state, path)

        return {
            "consumer_id": CONSUMER_ID,
            "source": COLLECTION_URL,
            "cursor": newest_seen,
            "processed": processed,
            "dereferenced": derefs,
            "added": added,
            "updated": updated,
            "removed": removed,
            "pool": len(candidates),
            "paintings": paintings,
        }


def _met_id(url: str) -> str | None:
    # The activity's object id embeds the Metropolitan object URL percent-encoded
    # (…/records/https%3A%2F%2F…%2Fobjects%2F544864), so unquote before matching.
    m = _MET_ID_RE.search(unquote(url or ""))
    return m.group(1) if m else None


# --- Callback (push) consume ------------------------------------------------ #
def apply_activity(activity: dict[str, Any], path: str | None = None) -> dict[str, Any]:
    """Apply a single pushed activity to the pool — the callback's real work.

    Mirrors one iteration of ``refresh``'s loop, but driven by an inbound
    ``Create`` / ``Update`` / ``Delete`` rather than the feed walk. Only
    Meta-Museum-host object ids are dereferenced (SSRF guard). Advances the
    cursor when the activity is newer than what we've seen. Thread-safe. Raises
    ``requests.RequestException`` if a needed dereference fails (the route maps
    that to 502 so Meta Museum can retry).
    """
    path = path or STATE_PATH
    action = (activity.get("type") or "").lower()
    if action not in {e.lower() for e in ACCEPTED_EVENTS}:
        return {"status": "ignored", "reason": f"unsupported type: {activity.get('type')!r}"}

    obj = activity.get("object") or {}
    obj_id = obj.get("id", "")
    met_id = _met_id(obj_id)
    if not met_id:
        return {"status": "ignored", "reason": "no Metropolitan object id in activity"}
    when = _activity_time(activity)

    with _lock:
        state = load_state(path)
        candidates: dict[str, Any] = dict(state.get("candidates", {}))

        if action == "delete":
            existed = candidates.pop(met_id, None) is not None
            outcome = "removed" if existed else "absent"
        else:
            if not _is_met_url(obj_id):
                return {"status": "rejected", "reason": "object id host not allowed"}
            parsed = parse_record(_get(obj_id))  # may raise; route -> 502
            is_new = met_id not in candidates
            candidates[met_id] = {
                "met_id": met_id,
                "record": obj_id,
                "changed_at": when,
                "activity": activity.get("type"),
                **parsed,
            }
            outcome = "added" if is_new else "updated"

        candidates = _evict_to_cap(candidates)
        cursor = state.get("cursor")
        if when and (cursor is None or when > cursor):
            cursor = when
        paintings = sum(1 for c in candidates.values() if c.get("is_painting"))
        state.update(
            {
                "cursor": cursor,
                "updated_at": _now_iso(),
                "counts": {"pool": len(candidates), "paintings": paintings},
                "candidates": candidates,
            }
        )
        _save_state(state, path)

    return {
        "status": "ok",
        "type": activity.get("type"),
        "met_id": met_id,
        "outcome": outcome,
        "pool": len(candidates),
        "paintings": paintings,
    }


# --- Read helpers (what Daily's routes expose) ----------------------------- #
def summary(path: str | None = None, sample: int = 5) -> dict[str, Any]:
    """A JSON-friendly status view of the pool without triggering a fetch."""
    state = load_state(path)
    cands = list(state.get("candidates", {}).values())
    cands.sort(key=lambda c: c.get("changed_at") or "", reverse=True)
    return {
        "consumer_id": state.get("consumer_id"),
        "source": state.get("source"),
        "cursor": state.get("cursor"),
        "updated_at": state.get("updated_at"),
        "counts": state.get("counts", {"pool": 0, "paintings": 0}),
        "sample": cands[:sample],
    }


def pick(
    seed: str | None = None, paintings_only: bool = True, path: str | None = None
) -> dict[str, Any] | None:
    """Draw one candidate from the pool (deterministic for a given ``seed``).

    This is the "selection queue" surface: Daily can offer a painting as a
    supplementary artwork-of-the-day alongside the Wikidata birthday pick.
    Returns ``None`` if the pool is empty.
    """
    import random

    state = load_state(path)
    pool = list(state.get("candidates", {}).values())
    if paintings_only:
        pool = [c for c in pool if c.get("is_painting")] or pool
    if not pool:
        return None
    pool.sort(key=lambda c: c.get("met_id") or "")  # stable order for the seed
    return random.Random(seed).choice(pool)


if __name__ == "__main__":
    result = refresh()
    print(json.dumps(result, indent=2, ensure_ascii=False))
