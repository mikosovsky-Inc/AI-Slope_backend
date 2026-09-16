from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.core.config import Settings
from app.core.security import create_access_token, decode_access_token, password_hasher
from app.models.user import User

CREDENTIALS = {"email": "Creator@example.com", "password": "a-long-test-password"}


def register(client):
    return client.post("/api/v1/auth/register", json=CREDENTIALS)


def login(client, **changes):
    return client.post("/api/v1/auth/login", json=CREDENTIALS | changes)


def test_register_login_me(client, db):
    response = register(client)
    assert response.status_code == 201
    assert response.json()["email"] == "creator@example.com"
    assert response.json()["role"] == "admin"
    assert "password" not in response.text
    user = db.exec(select(User)).one()
    assert user.password_hash.startswith("$argon2id$")
    assert password_hasher.verify(CREDENTIALS["password"], user.password_hash)
    token = login(client)
    assert token.status_code == 200
    assert token.headers["cache-control"] == "no-store"
    assert token.json()["expires_in"] == 1800
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token.json()['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json() == response.json()


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "wrong"},
        {"email": "unknown@example.com"},
    ],
)
def test_invalid_login(client, changes):
    register(client)
    response = login(client, **changes)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "invalid"},
        {"password": "tiny-pass"},
        {"password": "x" * 129},
        {"is_active": True},
        {"role": "admin"},
    ],
)
def test_registration_validation_does_not_expose_password(client, changes):
    data = CREDENTIALS | changes
    response = client.post("/api/v1/auth/register", json=data)
    assert response.status_code == 422
    assert data["password"] not in response.text
    assert all("input" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("authorization", [None, "Basic abc", "Bearer garbage"])
def test_missing_or_invalid_bearer(client, authorization):
    headers = {"Authorization": authorization} if authorization else {}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"exp": 1},
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"token_type": "refresh"},
        {"sub": "not-a-uuid"},
        {"sub": str(uuid4())},
        {"iat": 9999999999},
        {"exp": None},
    ],
)
def test_rejects_bad_claims(client, settings, change):
    user_id = register(client).json()["id"]
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + 300,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "token_type": "access",
    } | change
    if payload["exp"] is None:
        del payload["exp"]
    token = jwt.encode(payload, settings.jwt_secret_key.get_secret_value(), algorithm="HS256")
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_wrong_signature(client, settings):
    user = register(client).json()
    from uuid import UUID

    token = create_access_token(UUID(user["id"]), settings).access_token
    payload = jwt.decode(token, options={"verify_signature": False})
    forged = jwt.encode(payload, "wrong-key" * 8, algorithm="HS256")
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code
        == 401
    )


def test_inactive_user_cannot_login_or_use_token(client, db):
    register(client)
    token = login(client).json()["access_token"]
    user = db.exec(select(User)).one()
    user.is_active = False
    db.commit()
    assert login(client).status_code == 401
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )


def test_settings_read_dotenv_and_hide_secret(tmp_path):
    env = tmp_path / ".env"
    secret = "a-unique-test-secret" * 3
    env.write_text(f"DATABASE_URL=postgresql+psycopg://u:p@localhost/db\nJWT_SECRET_KEY={secret}\n")
    settings = Settings(_env_file=env)
    assert settings.jwt_secret_key.get_secret_value() == secret
    assert secret not in repr(settings)


def test_rejects_short_secret():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://u:p@localhost/db",
            jwt_secret_key="short",
        )


def test_token_roundtrip(settings):
    user_id = uuid4()
    token = create_access_token(user_id, settings)
    assert decode_access_token(token.access_token, settings).sub == user_id


def test_later_users_get_user_role_even_when_admin_is_inactive(client, db):
    first = register(client)
    assert first.json()["role"] == "admin"
    admin = db.exec(select(User)).one()
    admin.is_active = False
    db.commit()
    for email in ["second@example.com", "third@example.com"]:
        response = client.post("/api/v1/auth/register", json=CREDENTIALS | {"email": email})
        assert response.status_code == 201
        assert response.json()["role"] == "user"
        token = login(client, email=email).json()["access_token"]
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["role"] == "user"


def test_cannot_request_admin_role_after_registration(client):
    register(client)
    response = client.post(
        "/api/v1/auth/register",
        json=CREDENTIALS | {"email": "second@example.com", "role": "admin"},
    )
    assert response.status_code == 422
