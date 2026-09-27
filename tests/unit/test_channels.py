from uuid import uuid4

import pytest

DATA = {
    "idea": "Polski kanał o dziwnych wydarzeniach historycznych",
    "language": "pl",
    "videos_per_day": 2,
    "budget_per_video_usd": "0.20",
}


@pytest.fixture
def headers(client):
    credentials = {"email": "owner@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"language": "zz"},
        {"language": "PL"},
        {"videos_per_day": 0},
        {"videos_per_day": 25},
        {"videos_per_day": 1.5},
        {"videos_per_day": True},
        {"budget_per_video_usd": "0"},
        {"budget_per_video_usd": "-1"},
        {"budget_per_video_usd": "100.01"},
        {"budget_per_video_usd": "0.00001"},
        {"budget_per_video_usd": "NaN"},
        {"idea": "   "},
        {"name": "   "},
        {"autopilot_mode": "full_auto"},
        {"owner_id": str(uuid4())},
        {"status": "active"},
    ],
)
def test_invalid_channel_input(client, headers, overrides):
    response = client.post("/api/v1/channels", headers=headers, json=DATA | overrides)
    assert response.status_code == 422
    assert client.get("/api/v1/channels", headers=headers).json()["total"] == 0


def test_patch_validation_keeps_existing_data(client, headers):
    created = client.post("/api/v1/channels", headers=headers, json=DATA).json()
    path = f"/api/v1/channels/{created['id']}"
    for patch in [
        {},
        {"name": None},
        {"budget_per_video_usd": None},
        {"blueprint": None},
        {"status": "active"},
        {"blueprint": {"configuration": {"formats": {"top5": 1, "story": 1}}}},
        {"blueprint": {"content_pillars": [{"name": "History"}, {"name": "history"}]}},
    ]:
        assert client.patch(path, headers=headers, json=patch).status_code == 422
    assert client.get(path, headers=headers).json() == created


@pytest.mark.parametrize(
    "method, suffix",
    [
        ("get", ""),
        ("post", ""),
        ("get", "/00000000-0000-0000-0000-000000000000"),
        ("patch", "/00000000-0000-0000-0000-000000000000"),
        ("delete", "/00000000-0000-0000-0000-000000000000"),
        ("post", "/00000000-0000-0000-0000-000000000000/activate"),
        ("post", "/00000000-0000-0000-0000-000000000000/pause"),
    ],
)
def test_channel_routes_require_login(client, method, suffix):
    response = client.request(method.upper(), f"/api/v1/channels{suffix}", json=DATA)
    assert response.status_code == 401


def test_pagination_validation(client, headers):
    for query in ["limit=0", "limit=101", "offset=-1"]:
        assert client.get(f"/api/v1/channels?{query}", headers=headers).status_code == 422
