"""Run from the backend venv with Playwright installed; uses an isolated SQLite database."""

import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

import main
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db


def run():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost/test",
        jwt_secret_key="browser-test-only-" + "x" * 48,
        cors_allowed_origins=["http://studio.local"],
    )
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def session():
        with Session(engine, expire_on_commit=False) as db:
            yield db

    main.app.dependency_overrides[get_settings] = lambda: settings
    main.app.dependency_overrides[get_db] = session
    main.get_settings = lambda: settings
    main.get_engine = lambda: engine
    frontend_path = Path(__file__).resolve().parents[2] / "AI-Slop_frontend" / "main.py"
    spec = importlib.util.spec_from_file_location("frontend_server", frontend_path)
    frontend = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = frontend
    spec.loader.exec_module(frontend)
    frontend.get_settings = lambda: frontend.Settings(
        _env_file=None, api_base_url="http://api.local"
    )
    with (
        TestClient(main.app) as client,
        TestClient(frontend.app) as ui,
        sync_playwright() as playwright,
    ):
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        errors = []

        def forward(route):
            request = route.request
            url = urlsplit(request.url)
            target = client if url.hostname == "api.local" else ui
            response = target.request(
                request.method,
                url.path + ("?" + url.query if url.query else ""),
                headers=request.headers,
                content=request.post_data_buffer,
                follow_redirects=True,
            )
            route.fulfill(
                status=response.status_code, headers=dict(response.headers), body=response.content
            )

        context.route("http://studio.local/**", forward)
        context.route("http://api.local/**", forward)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto("http://studio.local/")
        expect(page).to_have_url("http://studio.local/register")
        expect(page.locator("#form-title")).to_have_text("Zacznij swoje studio.")
        with TemporaryDirectory(prefix="ai-slop-preview-") as screenshots:
            page.screenshot(path=f"{screenshots}/desktop.png", full_page=True)
            # Persist review screenshots outside the repository.
            Path("/tmp/ai-slop-desktop.png").write_bytes(
                Path(f"{screenshots}/desktop.png").read_bytes()
            )
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path="/tmp/ai-slop-mobile.png", full_page=True)
        page.locator("#email").fill("creator@example.com")
        page.locator("#password").fill("a-long-test-password")
        page.locator("#confirm-password").fill("different-password")
        page.locator("#submit-button").click()
        expect(page.locator("#message")).to_contain_text("Hasła nie są takie same")
        page.locator("#confirm-password").fill("a-long-test-password")
        page.locator("#submit-button").click()
        expect(page).to_have_url("http://studio.local/login")
        expect(page.locator("#message")).to_contain_text("Konto utworzone")
        page.locator("#password").fill("wrong-password")
        page.locator("#submit-button").click()
        expect(page.locator("#message")).to_contain_text("Nieprawidłowy e-mail lub hasło")
        page.locator("#password").fill("a-long-test-password")
        page.locator("#toggle-password").click()
        expect(page.locator("#password")).to_have_attribute("type", "text")
        page.locator("#submit-button").click()
        expect(page).to_have_url("http://studio.local/app")
        expect(page.locator("#account-role")).to_contain_text("ADMINISTRATOR")
        page.reload()
        expect(page.locator("#account-email")).to_have_text("creator@example.com")
        page.locator("#logout-button").click()
        assert page.evaluate("sessionStorage.getItem('ai-slop.access-token')") is None
        page.goto("http://studio.local/")
        expect(page).to_have_url("http://studio.local/login")
        expect(page.locator("#form-title")).to_have_text("Witaj ponownie.")
        page.locator("#switch-link").click()
        expect(page.locator("#setup-note")).to_be_hidden()
        page.locator("#email").fill("second@example.com")
        page.locator("#password").fill("a-long-test-password")
        page.locator("#confirm-password").fill("a-long-test-password")
        page.locator("#submit-button").click()
        expect(page).to_have_url("http://studio.local/login")
        page.locator("#password").fill("a-long-test-password")
        page.locator("#submit-button").click()
        expect(page.locator("#account-role")).to_have_text("↳ UŻYTKOWNIK")
        page.locator("#logout-button").click()
        page.route("**/api/v1/auth/setup", lambda route: route.abort())
        page.reload()
        expect(page.locator("#retry-button")).to_be_visible()
        expect(page.locator("#message")).to_contain_text("Nie udało się połączyć")
        page.unroute("**/api/v1/auth/setup")
        page.locator("#retry-button").click()
        expect(page.locator("#form-title")).to_have_text("Witaj ponownie.")
        assert not errors, errors
        browser.close()
    print("Browser checks passed: routing, registration, login, roles, session, errors, mobile.")


if __name__ == "__main__":
    run()
