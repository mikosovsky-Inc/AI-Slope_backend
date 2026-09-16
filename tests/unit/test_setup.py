import pytest


def test_setup_for_empty_database(client):
    response = client.get("/api/v1/auth/setup")
    assert response.json() == {"registration_required": True}
    assert response.headers["cache-control"] == "no-store"


def test_setup_for_existing_user(client):
    client.post(
        "/api/v1/auth/register",
        json={"email": "creator@example.com", "password": "a-long-test-password"},
    ).raise_for_status()
    assert client.get("/api/v1/auth/setup").json() == {"registration_required": False}


@pytest.mark.parametrize("path", ["/", "/login", "/register", "/app", "/assets/styles/main.css"])
def test_backend_does_not_serve_frontend(client, path):
    assert client.get(path).status_code == 404


def test_cors_allows_frontend_authorization_header(client):
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    response = client.get("/api/v1/auth/me", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_unknown_origin(client):
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
