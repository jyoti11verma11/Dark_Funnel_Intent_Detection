"""Backend tests for Dark Funnel Intent Detection app."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://funnel-signals.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- Stats ---
def test_stats(client):
    r = client.get(f"{API}/stats", timeout=30)
    assert r.status_code == 200
    d = r.json()
    for k in ["total_events", "matched_events", "high_intent_events", "alerts", "target_accounts", "alert_threshold"]:
        assert k in d, f"missing {k}"
    assert d["alert_threshold"] == 70
    assert d["target_accounts"] >= 20
    assert d["total_events"] >= 200
    assert d["matched_events"] >= 100
    assert d["high_intent_events"] >= 40


# --- Accounts CRUD ---
def test_list_accounts(client):
    r = client.get(f"{API}/accounts", timeout=30)
    assert r.status_code == 200
    accs = r.json()
    assert len(accs) >= 20
    a0 = accs[0]
    for k in ["company_name", "domain", "industry", "crm_owner", "tier", "id"]:
        assert k in a0


def test_create_and_delete_account(client):
    domain = f"testco-{uuid.uuid4().hex[:6]}.example.com"
    payload = {"company_name": "TEST_Co", "domain": domain, "industry": "Test", "crm_owner": "TestOwner", "tier": "B"}
    r = client.post(f"{API}/accounts", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    acc = r.json()
    aid = acc["id"]
    assert acc["domain"] == domain

    # verify via GET list
    r2 = client.get(f"{API}/accounts", timeout=30)
    assert any(a["id"] == aid for a in r2.json())

    # duplicate -> 409
    r3 = client.post(f"{API}/accounts", json=payload, timeout=30)
    assert r3.status_code == 409

    # delete
    r4 = client.delete(f"{API}/accounts/{aid}", timeout=30)
    assert r4.status_code == 200
    assert r4.json().get("deleted") == aid

    # verify gone
    r5 = client.get(f"{API}/accounts", timeout=30)
    assert not any(a["id"] == aid for a in r5.json())


# --- Event ingest: high intent, matched ---
def test_event_high_intent_matched_and_alert(client):
    stats_before = client.get(f"{API}/stats", timeout=30).json()
    alerts_before = stats_before["alerts"]

    payload = {"ip_address": "74.101.10.5", "page": "/pricing", "session_duration_sec": 180}
    r = client.post(f"{API}/events", json=payload, timeout=60)
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["resolved_company"] is not None
    # Should match target account Acme Corp
    assert ev["matched_account_id"] is not None
    assert ev["intent_score"] >= 70
    assert (ev["resolved_company"] or "").lower().startswith("acme")

    # Alert should have fired -- give it a moment (Claude ~2s)
    time.sleep(4)
    stats_after = client.get(f"{API}/stats", timeout=30).json()
    assert stats_after["alerts"] > alerts_before, "alert count did not increase"

    # Verify alert appears in list
    r2 = client.get(f"{API}/alerts?limit=10", timeout=30)
    assert r2.status_code == 200
    alerts = r2.json()
    assert any(a["event_id"] == ev["id"] for a in alerts)


# --- Event ingest: residential, no match ---
def test_event_residential_no_match(client):
    payload = {"ip_address": "192.168.1.5", "page": "/blog/foo", "session_duration_sec": 10}
    r = client.post(f"{API}/events", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["resolved_traffic_type"] == "residential"
    assert ev["matched_account_id"] is None


# --- Events filter high_intent_only ---
def test_events_high_intent_filter(client):
    r = client.get(f"{API}/events?high_intent_only=true&limit=50", timeout=30)
    assert r.status_code == 200
    for e in r.json():
        assert e["intent_score"] >= 70


# --- Matched visits aggregation ---
def test_matched_visits(client):
    r = client.get(f"{API}/visits/matched", timeout=60)
    assert r.status_code == 200
    items = r.json()
    assert len(items) > 0
    it = items[0]
    for k in ["account_id", "company_name", "domain", "industry", "crm_owner", "tier",
              "pages_viewed", "visit_count", "max_score", "total_duration_sec", "last_seen", "latest_event_id"]:
        assert k in it, f"missing {k} in matched visit"
    assert isinstance(it["pages_viewed"], list)
    # sorted by max_score desc
    scores = [i["max_score"] for i in items]
    assert scores == sorted(scores, reverse=True)

    # high_intent_only
    r2 = client.get(f"{API}/visits/matched?high_intent_only=true", timeout=60)
    assert r2.status_code == 200
    for i in r2.json():
        assert i["max_score"] >= 70


# --- Alerts list ---
def test_alerts_list(client):
    r = client.get(f"{API}/alerts?limit=50", timeout=30)
    assert r.status_code == 200
    alerts = r.json()
    assert len(alerts) >= 40
    a0 = alerts[0]
    assert a0.get("summary")  # non-empty
    assert a0["threshold"] == 70


# --- Regenerate alerts idempotency ---
def test_regenerate_idempotent(client):
    # capture pre-count
    before = client.get(f"{API}/stats", timeout=30).json()["alerts"]
    r = client.post(f"{API}/alerts/regenerate", timeout=300)
    assert r.status_code == 200
    d = r.json()
    assert "fired" in d and "total_high_intent" in d
    after = client.get(f"{API}/stats", timeout=30).json()["alerts"]
    # After regenerate, alerts count == before + fired
    assert after == before + d["fired"]

    # Run again -- should fire 0 new alerts
    r2 = client.post(f"{API}/alerts/regenerate", timeout=300)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["fired"] == 0, f"expected idempotent, got fired={d2['fired']}"
