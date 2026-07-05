"""Tests for the Meta Museum ActivityStreams consumer (met_activity.py) + routes.

The feed and record fetches (met_activity._get) are stubbed with monkeypatch, so
these exercise the real walk / reconciliation / parse logic with zero network.
"""

import json

import pytest

import app as APP
import met_activity as M

# --- canned Meta Museum feed ------------------------------------------------ #
COLLECTION = "https://www.metamuseum.org/api/activity/collection?provider=met"
PAGE0 = "https://www.metamuseum.org/api/activity/page/0?provider=met"
PAGE1 = "https://www.metamuseum.org/api/activity/page/1?provider=met"

# Object ids embed the Metropolitan object URL percent-encoded, like production.
REC = "https://www.metamuseum.org/api/records/https%3A%2F%2Fcollectionapi.metmuseum.org%2Fpublic%2Fcollection%2Fv1%2Fobjects%2F"


def _act(kind, obj_id, when):
    return {"type": kind, "endTime": when, "object": {"id": obj_id, "type": "HumanMadeObject"}}


def _record(label, painting, artist="Claude Monet", date="1867"):
    return {
        "record": {
            "type": "HumanMadeObject",
            "_label": label,
            "identified_by": [{"type": "Name", "content": label}],
            "classified_as": [{"_label": "Painting" if painting else "Vase"}],
            "produced_by": {
                "carried_out_by": [
                    {"id": "http://vocab.getty.edu/ulan/500019484", "_label": artist}
                ],
                "timespan": {"_label": date},
            },
            "representation": [],
        }
    }


# Two pages, oldest-to-newest within each; page/1 is `last`, prev -> page/0.
FEED = {
    COLLECTION: {"type": "OrderedCollection", "first": {"id": PAGE0}, "last": {"id": PAGE1}},
    PAGE0: {
        "type": "OrderedCollectionPage",
        "prev": None,
        "orderedItems": [
            _act("Create", REC + "100", "2026-01-01T00:00:00.000Z"),
        ],
    },
    PAGE1: {
        "type": "OrderedCollectionPage",
        "prev": {"id": PAGE0},
        "orderedItems": [
            _act("Create", REC + "200", "2026-02-01T00:00:00.000Z"),  # painting
            _act("Create", REC + "300", "2026-03-01T00:00:00.000Z"),  # vase (not painting)
        ],
    },
    REC + "100": _record("Garden at Sainte-Adresse", True),
    REC + "200": _record("Water Lilies", True),
    REC + "300": _record("Attic Vase", False, artist="Unknown", date="500 B.C."),
}


@pytest.fixture
def stub_feed(monkeypatch):
    monkeypatch.setattr(M, "_get", lambda url: FEED[url])


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "met_candidates.json")


# --- unit: parsing / id extraction ----------------------------------------- #
def test_met_id_handles_percent_encoding():
    assert M._met_id(REC + "437133") == "437133"
    assert M._met_id("https://x/objects/544864") == "544864"
    assert M._met_id("nonsense") is None


def test_parse_record_unwraps_envelope_and_extracts_fields():
    p = M.parse_record(_record("Water Lilies", True))
    assert p["title"] == "Water Lilies"
    assert p["artist"] == "Claude Monet"
    assert p["is_painting"] is True
    assert "ulan" in p["artist_authority"]
    assert p["date"] == "1867"


def test_parse_record_flags_non_paintings():
    assert M.parse_record(_record("Attic Vase", False))["is_painting"] is False


# --- walk / reconciliation -------------------------------------------------- #
def test_walk_back_is_newest_first(stub_feed):
    times = [M._activity_time(a) for a in M.walk_back(None)]
    assert times == sorted(times, reverse=True)  # page/1 newest item first


def test_walk_back_stops_at_cursor(stub_feed):
    seen = list(M.walk_back("2026-02-01T00:00:00.000Z"))
    # Only the strictly-newer activity (300) survives; cursor + older are skipped.
    assert [M._met_id(a["object"]["id"]) for a in seen] == ["300"]


def test_refresh_consumes_then_is_idempotent(stub_feed, state_path):
    first = M.refresh(path=state_path)
    assert first["added"] == 3
    assert first["pool"] == 3
    assert first["paintings"] == 2  # 100 + 200; the vase is excluded
    assert first["cursor"] == "2026-03-01T00:00:00.000Z"

    second = M.refresh(path=state_path)
    assert second["processed"] == 0 and second["added"] == 0
    assert second["pool"] == 3


def test_refresh_applies_deletes(stub_feed, state_path):
    M.refresh(path=state_path)
    # A later delete of object 200 removes it from the pool.
    later = dict(FEED)
    later[PAGE1] = {
        "type": "OrderedCollectionPage",
        "prev": {"id": PAGE0},
        "orderedItems": [_act("Delete", REC + "200", "2026-04-01T00:00:00.000Z")],
    }
    # swap the feed for the delete run
    orig = M._get
    try:
        M._get = lambda url: later[url]  # type: ignore[assignment]
        res = M.refresh(path=state_path)
    finally:
        M._get = orig  # type: ignore[assignment]
    assert res["removed"] == 1
    state = json.loads(open(state_path, encoding="utf-8").read())
    assert "200" not in state["candidates"]


def test_pick_is_deterministic_for_a_seed(stub_feed, state_path):
    M.refresh(path=state_path)
    a = M.pick(seed="2026-07-04", path=state_path)
    b = M.pick(seed="2026-07-04", path=state_path)
    assert a["met_id"] == b["met_id"]
    assert a["is_painting"] is True  # paintings_only default


def test_pick_empty_pool_returns_none(state_path):
    assert M.pick(path=state_path) is None


# --- routes ----------------------------------------------------------------- #
@pytest.fixture
def client(monkeypatch, tmp_path):
    APP.app.config["TESTING"] = True
    APP.limiter.enabled = False
    monkeypatch.setattr(M, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(M, "_get", lambda url: FEED[url])
    return APP.app.test_client()


def test_route_refresh_then_status_then_pick(client):
    r = client.post("/api/consumer/met/refresh")
    assert r.status_code == 200
    body = r.get_json()
    assert body["consumer_id"] == "daily-metahistorybook-prod"
    assert body["pool"] == 3

    s = client.get("/api/consumer/met").get_json()
    assert s["counts"]["pool"] == 3
    assert s["source"] == COLLECTION

    p = client.get("/api/consumer/met/pick?seed=x").get_json()
    assert p["status"] == "success"
    assert p["candidate"]["is_painting"] is True


def test_route_pick_empty_is_404(client):
    assert client.get("/api/consumer/met/pick").status_code == 404


# --- outbound headers ------------------------------------------------------- #
def test_outbound_sends_both_identity_headers():
    assert M._HEADERS["x-linked-art-consumer-id"] == "daily-metahistorybook-prod"
    assert M._HEADERS["User-Agent"].startswith("daily-metahistorybook-prod (")


# --- SSRF guard ------------------------------------------------------------- #
def test_is_met_url_allows_only_met_host():
    assert M._is_met_url("https://www.metamuseum.org/api/records/x") is True
    assert M._is_met_url("https://metamuseum.org/x") is True
    assert M._is_met_url("https://evil.example.com/x") is False
    assert M._is_met_url("https://metamuseum.org.evil.com/x") is False


# --- callback (push) -------------------------------------------------------- #
def test_apply_activity_create_update_delete(stub_feed, state_path):
    create = _act("Create", REC + "200", "2026-06-01T00:00:00.000Z")
    r = M.apply_activity(create, path=state_path)
    assert r["status"] == "ok" and r["outcome"] == "added" and r["pool"] == 1

    # Same object again = update, not a duplicate.
    upd = _act("Update", REC + "200", "2026-06-02T00:00:00.000Z")
    r = M.apply_activity(upd, path=state_path)
    assert r["outcome"] == "updated" and r["pool"] == 1

    dele = _act("Delete", REC + "200", "2026-06-03T00:00:00.000Z")
    r = M.apply_activity(dele, path=state_path)
    assert r["outcome"] == "removed" and r["pool"] == 0


def test_apply_activity_rejects_foreign_host(state_path):
    no_id = {
        "type": "Create",
        "endTime": "2026-06-01T00:00:00.000Z",
        "object": {"id": "https://evil.example.com/foo/9"},
    }
    assert M.apply_activity(no_id, path=state_path)["status"] == "ignored"  # no object id
    wrong_host = {
        "type": "Create",
        "endTime": "2026-06-01T00:00:00.000Z",
        "object": {"id": "https://evil.example.com/public/collection/objects/9"},
    }
    assert M.apply_activity(wrong_host, path=state_path)["status"] == "rejected"  # met id, bad host


def test_apply_activity_ignores_unknown_type(state_path):
    act = _act("Announce", REC + "200", "2026-06-01T00:00:00.000Z")
    assert M.apply_activity(act, path=state_path)["status"] == "ignored"


def test_route_callback_get_handshake(client):
    r = client.get("/api/consumer/met/callback")
    assert r.status_code == 200
    body = r.get_json()
    assert body["consumer_id"] == "daily-metahistorybook-prod"
    assert body["accepts"] == ["Create", "Update", "Delete"]


def test_route_callback_post_applies_and_returns_202(client):
    act = _act("Create", REC + "200", "2026-06-01T00:00:00.000Z")
    r = client.post("/api/consumer/met/callback", json=act)
    assert r.status_code == 202
    body = r.get_json()
    assert body["status"] == "accepted"
    assert body["results"][0]["outcome"] == "added"
    # It landed in the pool.
    assert client.get("/api/consumer/met").get_json()["counts"]["pool"] == 1


def test_route_callback_bad_payload_is_400(client):
    r = client.post("/api/consumer/met/callback", data="not json", content_type="text/plain")
    assert r.status_code == 400


def test_route_callback_secret_enforced(client, monkeypatch):
    monkeypatch.setenv("AOTD_MET_CALLBACK_TOKEN", "s3cret")
    act = _act("Create", REC + "200", "2026-06-01T00:00:00.000Z")
    assert client.post("/api/consumer/met/callback", json=act).status_code == 401
    ok = client.post("/api/consumer/met/callback", json=act, headers={"x-webhook-secret": "s3cret"})
    assert ok.status_code == 202
