"""
Tests for GET /auth/me — Profile endpoint.

Follows the same pattern as test_auth.py (module-scoped fixtures, local SQLite,
TestClient from api.main).
"""
import json
import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.main import app
from api.auth_utils import get_password_hash, create_access_token

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def seed_profile_data():
    """
    Set up a user with reviews for profile testing.
    Creates: 1 user, 1 venue, 1 event, 1 seat, 2 reviews.
    """
    from api.database import engine

    user_id = "test-profile-user-001"
    venue_id = "tp-venue-001"
    event_id = "tp-event-001"
    seat_id = "tp-seat-001"

    with engine.begin() as conn:
        # Clean up any residual data
        conn.execute(text("DELETE FROM Reviews WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text("DELETE FROM SeatAggregates WHERE seat_id = :sid"), {"sid": seat_id})
        conn.execute(text("DELETE FROM Seats WHERE id = :sid"), {"sid": seat_id})
        conn.execute(text("DELETE FROM Events WHERE id = :eid"), {"eid": event_id})
        conn.execute(text("DELETE FROM Venues WHERE id = :vid"), {"vid": venue_id})
        conn.execute(text("DELETE FROM Users WHERE id = :uid"), {"uid": user_id})

        # Create user
        conn.execute(
            text("INSERT INTO Users (id, email, password_hash, is_incognito, created_at) "
                 "VALUES (:id, :email, :pw, :inc, :ca)"),
            {"id": user_id, "email": "profiletest@example.com",
             "pw": get_password_hash("testpassword123"), "inc": False,
             "ca": "2025-01-15 10:00:00"},
        )

        # Create venue
        conn.execute(
            text("INSERT INTO Venues (id, name, city, capacity) VALUES (:id, :n, :c, :cap)"),
            {"id": venue_id, "n": "ProfileTestVenue", "c": "Toronto", "cap": 20000},
        )

        # Create event
        conn.execute(
            text("INSERT INTO Events (id, venue_id, name, artist, genre, event_date) "
                 "VALUES (:id, :vid, :n, :a, :g, :d)"),
            {"id": event_id, "vid": venue_id, "n": "ProfileTestEvent",
             "a": "TestArtist", "g": "rock", "d": "2025-06-01"},
        )

        # Create seat
        conn.execute(
            text("INSERT INTO Seats (id, venue_id, section, row, seat_number) "
                 "VALUES (:id, :vid, :sec, :row, :sn)"),
            {"id": seat_id, "vid": venue_id, "sec": "Floor", "row": "A", "sn": "1"},
        )

        # Create 2 reviews by this user
        conn.execute(
            text("INSERT INTO Reviews (id, user_id, event_id, venue_id, seat_id, "
                 "rating_visual, rating_sound, rating_value, overall_rating, "
                 "price_paid, text, tags, created_at) "
                 "VALUES (:id, :uid, :eid, :vid, :sid, :rv, :rs, :rval, :ro, :pp, :txt, :tags, :ca)"),
            [
                {"id": "tp-review-001", "uid": user_id, "eid": event_id, "vid": venue_id,
                 "sid": seat_id, "rv": 5, "rs": 4, "rval": 3, "ro": 4, "pp": 80.0,
                 "txt": "Amazing concert!", "tags": json.dumps(["rock", "great-view"]),
                 "ca": "2025-06-02 20:00:00"},
                {"id": "tp-review-002", "uid": user_id, "eid": event_id, "vid": venue_id,
                 "sid": seat_id, "rv": 3, "rs": 5, "rval": 4, "ro": 4, "pp": 75.0,
                 "txt": "Sound was stellar.", "tags": json.dumps(["sound", "value"]),
                 "ca": "2025-06-03 21:00:00"},
            ],
        )

    token = create_access_token({"sub": user_id}, expires_delta=timedelta(hours=1))
    yield {"user_id": user_id, "token": token}

    # Teardown
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Reviews WHERE user_id = :uid"), {"uid": user_id})
        conn.execute(text("DELETE FROM SeatAggregates WHERE seat_id = :sid"), {"sid": seat_id})
        conn.execute(text("DELETE FROM Seats WHERE id = :sid"), {"sid": seat_id})
        conn.execute(text("DELETE FROM Events WHERE id = :eid"), {"eid": event_id})
        conn.execute(text("DELETE FROM Venues WHERE id = :vid"), {"vid": venue_id})
        conn.execute(text("DELETE FROM Users WHERE id = :uid"), {"uid": user_id})


# ---------------------------------------------------------------------------
# GET /auth/me — Authentication
# ---------------------------------------------------------------------------

def test_me_without_auth():
    """GET /auth/me without JWT should return 401."""
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_with_invalid_token():
    """GET /auth/me with a garbage token should return 401."""
    response = client.get("/auth/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert response.status_code == 401


def test_me_with_expired_token():
    """GET /auth/me with an expired token should return 401."""
    expired_token = create_access_token(
        {"sub": "test-profile-user-001"},
        expires_delta=timedelta(seconds=-1),
    )
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# GET /auth/me — User info
# ---------------------------------------------------------------------------

def test_me_returns_user_info(seed_profile_data):
    """GET /auth/me should return user email and metadata."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {seed_profile_data['token']}"},
    )
    assert response.status_code == 200
    data = response.json()

    assert "user" in data
    user = data["user"]
    assert user["id"] == seed_profile_data["user_id"]
    assert user["email"] == "profiletest@example.com"
    assert user["is_incognito"] is False
    assert user["created_at"] is not None


# ---------------------------------------------------------------------------
# GET /auth/me — Stats
# ---------------------------------------------------------------------------

def test_me_returns_stats(seed_profile_data):
    """GET /auth/me should return correct aggregate stats."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {seed_profile_data['token']}"},
    )
    assert response.status_code == 200
    stats = response.json()["stats"]

    assert stats["total_reviews"] == 2
    assert stats["avg_rating"] == 4.0  # (4 + 4) / 2
    assert stats["top_venue"] == "ProfileTestVenue"


# ---------------------------------------------------------------------------
# GET /auth/me — Reviews
# ---------------------------------------------------------------------------

def test_me_returns_reviews(seed_profile_data):
    """GET /auth/me should return the user's reviews in descending order."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {seed_profile_data['token']}"},
    )
    assert response.status_code == 200
    reviews = response.json()["reviews"]

    assert len(reviews) == 2
    # Most recent first
    assert reviews[0]["id"] == "tp-review-002"
    assert reviews[1]["id"] == "tp-review-001"


def test_me_reviews_include_venue_name(seed_profile_data):
    """Each review should have the venue_name populated via JOIN."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {seed_profile_data['token']}"},
    )
    reviews = response.json()["reviews"]
    assert all(r["venue_name"] == "ProfileTestVenue" for r in reviews)


def test_me_reviews_include_seat_info(seed_profile_data):
    """Each review should have section, row, seat_number from Seats JOIN."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {seed_profile_data['token']}"},
    )
    reviews = response.json()["reviews"]
    for r in reviews:
        assert r["section"] == "Floor"
        assert r["row"] == "A"
        assert r["seat_number"] == "1"


def test_me_reviews_include_tags(seed_profile_data):
    """Review tags should be returned as parsed lists, not raw JSON strings."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {seed_profile_data['token']}"},
    )
    reviews = response.json()["reviews"]
    # tp-review-002 is first (most recent)
    assert isinstance(reviews[0]["tags"], list)
    assert "sound" in reviews[0]["tags"]


def test_me_reviews_include_ratings(seed_profile_data):
    """Each review should include all rating fields."""
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {seed_profile_data['token']}"},
    )
    reviews = response.json()["reviews"]
    for r in reviews:
        assert "rating_visual" in r
        assert "rating_sound" in r
        assert "rating_value" in r
        assert "overall_rating" in r
        assert "price_paid" in r


# ---------------------------------------------------------------------------
# GET /auth/me — Empty profile (user with no reviews)
# ---------------------------------------------------------------------------

def test_me_empty_profile():
    """A user with no reviews should get empty reviews list and zero stats."""
    from api.database import engine
    empty_user_id = "test-profile-empty-001"

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Users WHERE id = :id"), {"id": empty_user_id})
        conn.execute(
            text("INSERT INTO Users (id, email, password_hash) VALUES (:id, :e, :p)"),
            {"id": empty_user_id, "e": "emptyprofile@example.com",
             "p": get_password_hash("password")},
        )

    token = create_access_token({"sub": empty_user_id}, expires_delta=timedelta(hours=1))

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()

    assert data["stats"]["total_reviews"] == 0
    assert data["stats"]["avg_rating"] == 0
    assert data["stats"]["top_venue"] is None
    assert data["reviews"] == []

    # Cleanup
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Users WHERE id = :id"), {"id": empty_user_id})


# ---------------------------------------------------------------------------
# Full register → profile flow (integration)
# ---------------------------------------------------------------------------

def test_register_then_get_profile():
    """Register a new user, then use the returned token to access /auth/me."""
    email = "profileflow@example.com"
    password = "flowpassword123"

    # Cleanup
    from api.database import engine
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Users WHERE email = :e"), {"e": email})

    # Register
    reg_response = client.post("/auth/register", json={"email": email, "password": password})
    assert reg_response.status_code == 200
    token = reg_response.json()["access_token"]

    # Get profile with the registration token
    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_response.status_code == 200
    data = me_response.json()

    assert data["user"]["email"] == email
    assert data["stats"]["total_reviews"] == 0
    assert data["reviews"] == []

    # Cleanup
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Users WHERE email = :e"), {"e": email})


def test_login_then_get_profile():
    """Register, login, then use the login token to access /auth/me."""
    email = "loginprofile@example.com"
    password = "loginpassword123"

    from api.database import engine
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Users WHERE email = :e"), {"e": email})

    # Register
    client.post("/auth/register", json={"email": email, "password": password})

    # Login
    login_response = client.post("/auth/login", json={"email": email, "password": password})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Get profile
    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_response.status_code == 200
    assert me_response.json()["user"]["email"] == email

    # Cleanup
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM Users WHERE email = :e"), {"e": email})
