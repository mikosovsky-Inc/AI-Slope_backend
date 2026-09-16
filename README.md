# AI-Slop backend

FastAPI + PostgreSQL, schematy API Pydantic, konfiguracja `pydantic-settings`,
hasła Argon2id i tokeny dostępu JWT (HS256). Modele bazy i sesje korzystają z SQLModel (opartego na SQLAlchemy i Pydantic),
a migracje z Alembic. E-maile są zapisywane małymi literami i unikalne.

## Uruchomienie lokalne

Wymagania: Python 3.12+, uv oraz działający Docker Compose.

```sh
uv sync
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Wpisz wygenerowaną wartość do `JWT_SECRET_KEY` w `.env`. Hasło bazy w
`DATABASE_URL` musi odpowiadać `POSTGRES_PASSWORD` (znaki specjalne w URL
należy zakodować). Przykładowe hasło Postgresa służy do pracy lokalnej.

```sh
docker compose up -d --wait postgres
uv run alembic upgrade head
uv run uvicorn main:app --reload
```

Swagger UI: http://localhost:8000/docs. Migracje uruchamia się jawnie;
API nie tworzy tabel automatycznie. `.env` jest ignorowany przez Git.

## API

| Metoda | Ścieżka | Działanie |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | Rejestracja, odpowiedź 201 z publicznymi danymi użytkownika |
| POST | `/api/v1/auth/login` | Logowanie, odpowiedź z `access_token`, `token_type`, `expires_in` (sekundy) |
| GET | `/api/v1/auth/me` | Dane zalogowanego użytkownika, wymaga Bearer JWT |

Rejestracja i logowanie przyjmują JSON:

```json
{"email": "creator@example.com", "password": "moje-dlugie-haslo-123"}
```

Hasło przy rejestracji: 12–128 znaków. Rejestracja nie loguje automatycznie.
Po logowaniu przekaż `Authorization: Bearer <access_token>` lub wklej token
w przycisku **Authorize** w Swagger UI.

Błędy: 409 dla zajętego e-maila, 422 dla błędnych danych, 401 dla błędnego
logowania, nieważnego tokena lub nieaktywnego użytkownika. Odpowiedzi nie
zawierają hasła ani jego skrótu, także przy błędach walidacji.
Token wygasa domyślnie po 30 minutach; API weryfikuje podpis, wystawcę,
odbiorcę, typ tokena i aktywność użytkownika w bazie.

## Testy

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Testy API korzystają z izolowanej bazy SQLite w pamięci. Test integracyjny
PostgreSQL jest pomijany, dopóki nie ustawisz `TEST_DATABASE_URL`:

```sh
TEST_DATABASE_URL=postgresql+psycopg://ai_slop:ai_slop_local@localhost:5432/ai_slop uv run pytest tests/integration
```

Test tworzy losowy schemat, sprawdza migrację, rejestrację, konflikt e-maila,
logowanie i `/me`, następnie usuwa swój schemat. Konto testowe musi mieć prawo
`CREATE SCHEMA`; używaj bazy przeznaczonej do testowania.

## Zakres tego etapu

Konfiguracja `S3_*` jest przygotowana pod bucket SeaweedFS zgodny z S3.
Przesyłanie plików i uruchamianie SeaweedFS pozostają do kolejnego etapu.
Nie ma jeszcze odświeżania/unieważniania tokenów, resetowania hasła,
weryfikacji e-maila ani limitowania prób logowania. Przy publicznym
wdrożeniu należy skonfigurować HTTPS i ograniczenie liczby prób logowania.

Nowe modele bazodanowe definiuj jako `SQLModel` z `table=True` (przez wspólną
klasę `app.db.base.Base`). Schematy wejścia i odpowiedzi API pozostają oddzielne.

Referencje: [SQLModel](https://sqlmodel.tiangolo.com/tutorial/create-db-and-table/),
[FastAPI JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/),
[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

## GitHub Actions

Workflow `.github/workflows/tests.yml` uruchamia testy przy każdym `push`
i `pull_request`, na Pythonie 3.12 z tymczasowym PostgreSQL 17.
Instaluje zależności przez `uv sync --locked --dev`, a następnie uruchamia
cały zestaw testów. `TEST_DATABASE_URL` jest ustawiony w workflow, dzięki
czemu test integracyjny PostgreSQL również się wykonuje.
Nie wymaga sekretów repozytorium ani pliku `.env` — używa danych testowych.
Do repozytorium należy dołączyć `uv.lock`.


## Role użytkowników

Pierwsza udana rejestracja w pustej tabeli `users` otrzymuje rolę `admin`.
Każda kolejna otrzymuje `user`, także gdy administrator jest nieaktywny.
Rola jest nadawana przez backend; pole `role` w żądaniu rejestracji jest odrzucane.
Odpowiedzi rejestracji oraz `/api/v1/auth/me` zawierają `role`.

Migracja `0002` nadaje najstarszemu istniejącemu kontu rolę `admin`
(według `created_at`, a przy remisie UUID), pozostałym `user`.
Uruchom ją przez `uv run alembic upgrade head`.
Przy równoległych rejestracjach pierwszy zatwierdzony zapis otrzymuje `admin`;
[blokada PostgreSQL](https://www.postgresql.org/docs/17/explicit-locking.html)
serializuje sprawdzenie pustej tabeli i zapis. SQLite służy tylko do testów jednostkowych.
Role są na razie zapisywane i zwracane przez API; nie dodano endpointów administracyjnych.

## Oddzielny frontend

Backend hostuje wyłącznie API, domyślnie pod http://localhost:8000.
Frontend jest osobnym projektem i serwerem FastAPI na http://localhost:3000.
Backend nie wymaga plików ani instalacji projektu frontendowego.

W `.env` backendu ustaw dozwolone adresy frontendu:

```dotenv
CORS_ALLOWED_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
```

W `.env` frontendu ustaw `API_BASE_URL=http://localhost:8000`.
To adres osiągalny z przeglądarki użytkownika. Przy wdrożeniu ustaw rzeczywiste
adresy obu usług; adresy w CORS nie powinny mieć końcowego ukośnika.

Frontend pobiera `GET /api/v1/auth/setup` i wybiera rejestrację przy pustej bazie
lub logowanie, gdy istnieje konto. Logowanie, JWT i dane użytkowników obsługuje
wyłącznie backend. [Konfiguracja CORS](https://fastapi.tiangolo.com/tutorial/cors/).

Opcjonalny test obu aplikacji w Chromium, gdy repozytoria są obok siebie:

```sh
uv run --with playwright python -m playwright install chromium
uv run --with playwright python tests/browser_smoke.py
```

Test używa oddzielnych originów i izolowanej bazy SQLite w pamięci.
Nie korzysta z bazy skonfigurowanej w `.env`.
