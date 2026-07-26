"""Backend tests for Dark Funnel Intent Detection app (iteration 2 - JWT auth)."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
API = f"{BASE_URL}/api"


# ---------------------- Fixtures ----------------------
@pytest.fixture(scope="session")
def anon_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def auth_data(anon_client):
    """Create a new test user via signup and return token+user."""
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    password = "testpass123"
    r = anon_client.post(f"{API}/auth/signup", json={"email": email, "password": password, "name": "Test User"}, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "token" in d and "user" in d
    return {"token": d["token"], "user": d["user"], "email": email, "password": password}


@pytest.fixture(scope="session")
def client(auth_data):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {auth_data['token']}"})
    return s


# ---------------------- Auth tests ----------------------
def test_health_public(anon_client):
    r = anon_client.get(f"{API}/", timeout=15)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_stats_requires_auth(anon_client):
    r = anon_client.get(f"{API}/stats", timeout=15)
    assert r.status_code == 401


def test_signup_duplicate(anon_client, auth_data):
    r = anon_client.post(f"{API}/auth/signup", json={"email": auth_data["email"], "password": "anotherpass"}, timeout=15)
    assert r.status_code == 409


def test_signup_short_password(anon_client):
    email = f"TEST_{uuid.uuid4().hex[:6]}@example.com"
    r = anon_client.post(f"{API}/auth/signup", json={"email": email, "password": "abc"}, timeout=15)
    assert r.status_code == 400


def test_signup_invalid_email(anon_client):
    r = anon_client.post(f"{API}/auth/signup", json={"email": "notanemail", "password": "abcdef"}, timeout=15)
    assert r.status_code == 400


def test_login_success(anon_client, auth_data):
    r = anon_client.post(f"{API}/auth/login", json={"email": auth_data["email"], "password": auth_data["password"]}, timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d["token"] and d["user"]["email"] == auth_data["email"]


def test_login_wrong_password(anon_client, auth_data):
    r = anon_client.post(f"{API}/auth/login", json={"email": auth_data["email"], "password": "wrongpass1"}, timeout=15)
    assert r.status_code == 401


def test_login_nonexistent(anon_client):
    r = anon_client.post(f"{API}/auth/login", json={"email": "nobody-xyz@example.com", "password": "whatever1"}, timeout=15)
    assert r.status_code == 401


def test_me_requires_token(anon_client):
    r = anon_client.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 401


def test_me_invalid_token(anon_client):
    r = requests.get(f"{API}/auth/me", headers={"Authorization": "Bearer not.a.jwt"}, timeout=15)
    assert r.status_code == 401


def test_me_valid_token(client, auth_data):
    r = client.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d["email"] == auth_data["email"]


# ---------------------- All data endpoints require auth ----------------------
@pytest.mark.parametrize("path,method", [
    ("/stats", "GET"),
    ("/visits/matched", "GET"),
    ("/accounts", "GET"),
    ("/events", "GET"),
    ("/alerts", "GET"),
    ("/alerts/regenerate", "POST"),
])
def test_endpoints_require_auth(anon_client, path, method):
    r = anon_client.request(method, f"{API}{path}", timeout=30)
    assert r.status_code == 401, f"{method} {path} returned {r.status_code}"


# ---------------------- Stats ----------------------
def test_stats(client):
    r = client.get(f"{API}/stats", timeout=30)
    assert r.status_code == 200
    d = r.json()
    for k in ["total_events", "matched_events", "high_intent_events", "alerts", "target_accounts", "alert_threshold"]:
        assert k in d
    assert d["alert_threshold"] == 70
    assert d["target_accounts"] >= 20
    assert d["total_events"] >= 200


# ---------------------- Accounts CRUD ----------------------
def test_list_accounts(client):
    r = client.get(f"{API}/accounts", timeout=30)
    assert r.status_code == 200
    accs = r.json()
    assert len(accs) >= 20


def test_create_and_delete_account(client):
    domain = f"testco-{uuid.uuid4().hex[:6]}.example.com"
    payload = {"company_name": "TEST_Co", "domain": domain, "industry": "Test", "crm_owner": "TestOwner", "tier": "B"}
    r = client.post(f"{API}/accounts", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]

    r3 = client.post(f"{API}/accounts", json=payload, timeout=30)
    assert r3.status_code == 409

    r4 = client.delete(f"{API}/accounts/{aid}", timeout=30)
    assert r4.status_code == 200


# ---------------------- Events ----------------------
def test_event_high_intent_matched_and_alert(client):
    stats_before = client.get(f"{API}/stats", timeout=30).json()
    alerts_before = stats_before["alerts"]

    payload = {"ip_address": "74.101.10.5", "page": "/pricing", "session_duration_sec": 180}
    r = client.post(f"{API}/events", json=payload, timeout=60)
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["matched_account_id"] is not None
    assert ev["intent_score"] >= 70

    time.sleep(4)
    stats_after = client.get(f"{API}/stats", timeout=30).json()
    assert stats_after["alerts"] > alerts_before


def test_event_residential_no_match(client):
    payload = {"ip_address": "192.168.1.5", "page": "/blog/foo", "session_duration_sec": 10}
    r = client.post(f"{API}/events", json=payload, timeout=30)
    assert r.status_code == 200
    ev = r.json()
    assert ev["matched_account_id"] is None


def test_events_high_intent_filter(client):
    r = client.get(f"{API}/events?high_intent_only=true&limit=50", timeout=30)
    assert r.status_code == 200
    for e in r.json():
        assert e["intent_score"] >= 70


# ---------------------- Matched visits ----------------------
def test_matched_visits(client):
    r = client.get(f"{API}/visits/matched", timeout=60)
    assert r.status_code == 200
    items = r.json()
    assert len(items) > 0
    scores = [i["max_score"] for i in items]
    assert scores == sorted(scores, reverse=True)


# ---------------------- Alerts ----------------------
def test_alerts_list(client):
    r = client.get(f"{API}/alerts?limit=50", timeout=30)
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_regenerate_idempotent(client):
    r = client.post(f"{API}/alerts/regenerate", timeout=300)
    assert r.status_code == 200
    r2 = client.post(f"{API}/alerts/regenerate", timeout=300)
    assert r2.status_code == 200
    assert r2.json()["fired"] == 0
