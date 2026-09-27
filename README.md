# AI-Slop backend

FastAPI + PostgreSQL, schematy API Pydantic, konfiguracja `pydantic-settings`,
hasła Argon2id i tokeny dostępu JWT (HS256). Modele bazy i sesje korzystają z SQLModel (opartego na SQLAlchemy i Pydantic),
a migracje z Alembic. E-maile są zapisywane małymi literami i unikalne.

## Etap 1 — Foundation

Działający fundament modularnego monolitu: FastAPI, PostgreSQL, SQLModel,
Alembic, Redis, kontenery, health checks, logi JSON i obsługa błędów.
Istniejące rejestracja, logowanie JWT i role pozostają dostępne.
Kolejne etapy (kanały, AI, generowanie filmów) nie są jeszcze wdrożone.
Szczegóły: [docs/architecture.md](docs/architecture.md).

## Uruchomienie całego stacka

Wymagania: Docker i Docker Compose. W katalogu backendu:

```sh
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Jeśli `.env` już istnieje, uzupełnij go zamiast nadpisywać. Wstaw wygenerowany
klucz do `JWT_SECRET_KEY`. Domyślne dane PostgreSQL w przykładzie służą do pracy
lokalnej. Następnie:

```sh
docker compose up --build
```

Compose uruchamia dokładnie trzy usługi: `api`, `postgres`, `redis`.
API czeka na gotowość obu zależności, wykonuje `alembic upgrade head`,
następnie startuje Uvicorn. Błąd migracji zatrzymuje start API.
Dane PostgreSQL i Redis są przechowywane w nazwanych wolumenach.
Obraz API uruchamia się jako użytkownik bez uprawnień roota i nie zawiera `.env`.

Uruchomienie w tle i weryfikacja:

```sh
docker compose up --build -d --wait
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/ready
docker compose logs -f api
```

Oba endpointy zwracają przy sukcesie `{"status":"ok"}`.
`/health` sprawdza działanie procesu; `/ready` wykonuje `SELECT 1` w PostgreSQL
oraz `PING` w Redis i zwraca 503, gdy zależność jest niedostępna.
Po przywróceniu zależności `/ready` odzyskuje gotowość bez restartu API.
Swagger UI: http://localhost:8000/docs.

```sh
docker compose down
```

Powyższe zatrzymuje stack i zachowuje dane. `docker compose down --volumes`
usuwa też wszystkie dane obu usług — używaj tylko do świadomego resetu środowiska.

## Uruchomienie API poza Dockerem

Wymagania dodatkowe: Python 3.12+ i uv.

```sh
uv sync --no-active
docker compose up -d --wait postgres redis
uv run --no-active alembic upgrade head
uv run --no-active uvicorn main:app --reload --port 8000
```

W tym trybie `DATABASE_URL` i `REDIS_URL` wskazują `localhost`.
Nie uruchamiaj jednocześnie lokalnego API i kontenera API na tym samym porcie.

## Konfiguracja

`pydantic-settings` czyta `.env` i zmienne procesu (zmienne procesu mają priorytet).
Przykłady: [.env.example](.env.example). `.env` jest ignorowany przez Git.

| Zmienna | Znaczenie |
| --- | --- |
| `DATABASE_URL` | DSN PostgreSQL dla lokalnego API i Alembic, schemat `postgresql+psycopg` |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Dane inicjalizacji PostgreSQL w Compose |
| `DOCKER_DATABASE_URL` | Opcjonalny pełny DSN kontenera API; domyślnie budowany z `POSTGRES_*`, host `postgres` |
| `REDIS_URL` | Adres Redis lokalnie; Compose używa `redis://redis:6379/0` |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | Limit połączenia PostgreSQL, domyślnie 3 s |
| `DATABASE_STATEMENT_TIMEOUT_MS` | Limit zapytania PostgreSQL, domyślnie 5000 ms |
| `REDIS_TIMEOUT_SECONDS` | Limit połączenia i operacji Redis, domyślnie 2 s |
| `JWT_SECRET_KEY` | Wymagany klucz podpisu JWT, minimum 32 bajty |
| `JWT_ACCESS_TOKEN_MINUTES` | Czas ważności JWT, domyślnie 30 minut |
| `JWT_ISSUER`, `JWT_AUDIENCE` | Wystawca i odbiorca tokenów |
| `CORS_ALLOWED_ORIGINS` | Tablica JSON dozwolonych adresów frontendu |
| `LOG_LEVEL` | Poziom logów aplikacji, domyślnie `INFO` |
| `EXTERNAL_PROVIDERS_MODE` | `mock` (domyślnie) lub `live`; rezerwacja pod integracje późniejszych etapów |
| `API_PORT`, `POSTGRES_PORT`, `REDIS_PORT` | Porty hosta Compose: 8000, 5432, 6379 |
| `S3_*` | Rezerwacja konfiguracji SeaweedFS pod późniejszy etap storage |

Hasła zawierające znaki specjalne URL muszą być zakodowane procentowo w DSN.
W takim przypadku ustaw jawnie także `DOCKER_DATABASE_URL`. Porty wewnątrz
Compose zawsze wynoszą 5432 i 6379; `*_PORT` zmieniają wyłącznie porty hosta.
Zmiana `POSTGRES_PASSWORD` nie zmienia hasła istniejącej bazy w wolumenie.

## Migracje

Kontener API wykonuje migracje przy starcie (lokalny stack z jedną instancją API).
Ręcznie:

```sh
docker compose exec api alembic current
docker compose exec api alembic upgrade head
# Lokalny Python:
uv run --no-active alembic revision --autogenerate -m "describe change"
uv run --no-active alembic upgrade head
```

Przy przyszłym wdrożeniu wielu instancji migracje powinny być osobnym krokiem
wdrożenia, przed uruchomieniem API. Nie stosujemy `create_all()` w aplikacji.

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
uv run --no-active pytest
uv run --no-active ruff check .
uv run --no-active ruff format --check .
```

Testy jednostkowe używają SQLite w pamięci; mockujemy klienta zewnętrznego Redis,
a nie logikę health check. Testy integracyjne wymagają rzeczywistych usług:

```sh
TEST_DATABASE_URL=postgresql+psycopg://ai_slop:ai_slop_local@localhost:5432/ai_slop \
TEST_REDIS_URL=redis://localhost:6379/15 \
uv run --no-active pytest -ra
```

Ustaw DSN zgodny z własną bazą testową. Testy migracji tworzą losowe schematy,
sprawdzają migracje, rejestrację, role, równoległe rejestracje i logowanie,
następnie usuwają wyłącznie swoje schematy. Użytkownik bazy potrzebuje prawa
`CREATE SCHEMA`. Test Redis używa losowego klucza z TTL, bez `FLUSHDB`.
Bez `TEST_DATABASE_URL` i `TEST_REDIS_URL` odpowiednie testy są pomijane.
GitHub Actions uruchamia je z usługami PostgreSQL i Redis.

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
i `pull_request`, na Pythonie 3.12 z tymczasowym PostgreSQL 17 i Redis 7.4.
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


## TODO po etapie 1

- Etap 2: Channel, ChannelBlueprint, ContentPillar i CRUD z kontrolą właściciela.
- Etapy 3–10: adapter LLM, intelligence, research, pomysły, scenariusze i reżyseria.
- Etapy 11–16: SeaweedFS, adaptery GPU/TTS, Dramatiq, FFmpeg i kontrola jakości.
- Etapy 17–20: scheduler, panelowe API, budżety i rozszerzona obserwowalność.

Na tym etapie nie ma workerów ani schedulera do uruchomienia, dostawców AI,
pełnego video workflow ani polecenia seed/demo. Ustawienie `live` nie wykonuje
jeszcze żadnych płatnych operacji. Zostaną dodane i przetestowane w swoich etapach.
