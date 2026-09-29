# AI-Slop backend

FastAPI + PostgreSQL, schematy API Pydantic, konfiguracja `pydantic-settings`,
hasła Argon2id i tokeny dostępu JWT (HS256). Modele bazy i sesje korzystają z SQLModel (opartego na SQLAlchemy i Pydantic),
a migracje z Alembic. E-maile są zapisywane małymi literami i unikalne.

## Stan projektu — etapy 1–20

Modularny monolit FastAPI z JWT, PostgreSQL/SQLModel, Redis i SeaweedFS.
Działa analiza kanału, pomysły, scenariusze STORY/TOP5, Director, adaptery
Runpod/TTS, asynchroniczne zadania Dramatiq, renderer FFmpeg i techniczna kontrola
jakości z naprawą pojedynczych scen oraz osobny scheduler dziennego planu kanałów.
Po pozytywnym QC film otrzymuje stan `READY`.
Szczegóły: [docs/architecture.md](docs/architecture.md).

**Od etapu 14 operacje generowania zwracają `202` i zadanie, a wynik odbiera się
przez GET /api/v1/tasks/{id}.** Opisy wcześniejszych etapów dokumentują także
poprzedni kontrakt synchroniczny; aktualny przepływ jest w sekcji etapu 14.

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

Compose uruchamia `api`, `postgres`, `redis`, `seaweedfs`, `worker`, `dispatcher`
i `scheduler`. Scheduler domyślnie planuje aktywne kanały; w trybie live może zlecać
wywołania providerów. Wyłącza go `SCHEDULER_ENABLED=false`.
API czeka na gotowość obu zależności, wykonuje `alembic upgrade head`,
następnie startuje Uvicorn. Błąd migracji zatrzymuje start API.
Dane PostgreSQL, Redis i SeaweedFS są przechowywane w nazwanych wolumenach.
Worker i dispatcher startują po API, które jako jedyne stosuje migracje.
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
usuwa też dane PostgreSQL, Redis i SeaweedFS — używaj tylko do świadomego resetu środowiska.

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
| `EXTERNAL_PROVIDERS_MODE` | `mock` (domyślnie) lub `live` dla adapterów AI |
| `API_PORT`, `POSTGRES_PORT`, `REDIS_PORT` | Porty hosta Compose: 8000, 5432, 6379 |
| `S3_*` | Konfiguracja prywatnego storage S3/SeaweedFS |

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


## TODO po etapie 20

- Rozszerzona edycja gotowych filmów z wersjonowaniem assetów i ręczne odzyskiwanie niepewnych zadań.
- Integracja z zewnętrznym systemem telemetrii (np. Sentry/Prometheus), jeśli będzie potrzebna.
- Automatyczne połączenie Directora z generowaniem assetów i pierwszym renderem; seed/demo.

Worker, dispatcher i scheduler są uruchamiane w Compose. Nie ma jeszcze polecenia seed/demo.
Render uruchamiany ręcznie prowadzi już przez kontrolę jakości do READY.
W trybie `live` działający scheduler może zlecać płatne generowanie dla aktywnych
kanałów; `SCHEDULER_ENABLED=false` wyłącza tę automatyzację.


## Etap 2 — kanały

Nowa migracja: `0003_channel_domain`. Lokalnie wykonaj
`uv run --no-active alembic upgrade head`; w Dockerze przebuduj API:

```sh
docker compose up --build -d --wait
```

Wszystkie endpointy wymagają `Authorization: Bearer <access_token>`.
Użytkownik widzi wyłącznie własne kanały, także gdy ma rolę `admin`.
Próba odczytu lub modyfikacji obcego albo nieistniejącego kanału zwraca 404.

| Metoda | Endpoint | Działanie |
| --- | --- | --- |
| POST | `/api/v1/channels` | Tworzy kanał `draft` i bazowy blueprint, 201 |
| GET | `/api/v1/channels?limit=20&offset=0` | Lista właściciela: `items`, `total`, `limit`, `offset` |
| GET | `/api/v1/channels/{id}` | Kanał, blueprint i uporządkowane filary |
| PATCH | `/api/v1/channels/{id}` | Aktualizacja danych lub ręczna konfiguracja blueprintu |
| DELETE | `/api/v1/channels/{id}` | Usuwa kanał, blueprint i filary, 204 |
| POST | `/api/v1/channels/{id}/activate` | Ustawia `active` |
| POST | `/api/v1/channels/{id}/pause` | Ustawia `paused` |

Przykład utworzenia (TOKEN to token z endpointu logowania):

```sh
curl --fail-with-body http://localhost:8000/api/v1/channels \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"idea":"Polski kanał o dziwnych wydarzeniach historycznych","language":"pl","videos_per_day":2,"budget_per_video_usd":0.20}'
```

Walidacja etapu 2:

- `name`: opcjonalne, 1–120 znaków; domyślnie pierwsze 120 znaków opisu.
- `idea`: 10–4000 znaków po usunięciu skrajnych spacji.
- `language`: `pl` lub `en`; pozostałe języki zwracają 422.
- `videos_per_day`: liczba całkowita 1–24, domyślnie 2.
- `budget_per_video_usd`: większe od 0, do 100 USD, maksymalnie 4 miejsca po przecinku.
  W bazie NUMERIC, w Pythonie Decimal; odpowiedź JSON zawiera kwotę jako string.
- `autopilot_mode`: `manual` (domyślnie) lub `semi_auto`; `full_auto` jeszcze niedostępne.
- `status` i `owner_id`: nadawane wyłącznie przez backend, nie przez request.
- Lista: `limit` 1–100, `offset` >= 0; porządek od najnowszych (created_at, id).
- PATCH pomija nieprzesłane pola; jawne `null` i pusty PATCH dają 422.

Aktywacja i pauza są idempotentne. Powtórzenie tej samej akcji nie zmienia
`updated_at`. Zmiana statusu nie uruchamia jeszcze workera ani publikacji.

Blueprint jest tworzony atomowo z kanałem: format mix TOP5 0.7/STORY 0.3,
45 sekund, szybkie tempo, hook do 2 sekund, udział scen video 0.25.
To edytowalne wartości początkowe, nie wynik analizy AI. Filary początkowo są puste.
Opis, język, częstotliwość publikacji i budżet mają jedno źródło prawdy w Channel,
a ustawienia twórcze w ChannelBlueprint.configuration (JSON walidowany Pydantic).
Analiza AI jest dostępna przez endpoint opisany w etapie 4.

Przykład ręcznej konfiguracji przez PATCH:

```json
{
  "name": "Mroczne historie",
  "autopilot_mode": "semi_auto",
  "blueprint": {
    "configuration": {
      "target_audience": "Dorośli zainteresowani historią",
      "tone": "tajemniczy",
      "formats": {"top5": 0.7, "story": 0.3},
      "video_style": {"duration_target": 45, "pace": "fast", "hook_max_seconds": 2},
      "visual_style": {"description": "Ilustracje archiwalne", "video_scene_ratio": 0.25}
    },
    "content_pillars": [
      {"name": "Zagadki", "description": "Niewyjaśnione wydarzenia"},
      {"name": "Postacie", "description": "Mało znane biografie"}
    ]
  }
}
```

Przesłane `blueprint` zastępuje cały blueprint i listę filarów; brakujące ustawienia
otrzymują wartości domyślne. Pominięcie `blueprint` zachowuje go bez zmian.
Maksymalnie 20 filarów, unikalne nazwy bez rozróżniania wielkości liter,
pozycja wynika z kolejności listy. Format mix musi sumować się do 1.
Usuwanie kanału jest trwałe. Aktualizacja kanału i blueprintu jest jedną transakcją;
błąd zapisu filaru wycofuje także zmianę kanału.


## Etap 3 — integracja LLM

Kontrakt `app/shared/llm.py` udostępnia `LLMProvider`, `LLMRequest`, generyczny
`LLMResult[T]` i błędy niezależne od dostawcy. Schemat odpowiedzi jest modelem
Pydantic przekazywanym do `generate(request, response_model)`. Modele domenowe
nie importują SDK. Adapter OpenAI korzysta z Responses API, `responses.parse`,
`text_format` i `store=False`:
https://developers.openai.com/api/docs/guides/structured-outputs

Provider jest tworzony raz podczas startu FastAPI, dostępny przez `CurrentLLM`
i zamykany podczas shutdown. Interfejs jest synchroniczny — używaj go w zwykłych
endpointach `def` lub workerach; w `async def` przenieś wywołanie do threadpool.
Endpoint analizy z etapu 4 korzysta z tego providera; start API nie wykonuje
płatnych wywołań.

Domyślnie `EXTERNAL_PROVIDERS_MODE=mock`. Mock działa offline i wymaga jawnych
fixture dla danego schematu; brak fixture zgłasza błąd zamiast wymyślać odpowiedź.
Przykład użycia kontraktu (tak samo dla rzeczywistego providera):

```python
from pydantic import BaseModel, ConfigDict
from app.shared.llm import LLMRequest
from app.integrations.llm.mock import MockLLMProvider


class Title(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str


provider = MockLLMProvider({Title: {"title": "Tajemnice historii"}})
result = provider.generate(
    LLMRequest(instructions="Zaproponuj tytuł", prompt="Kanał historyczny"), Title
)
assert result.data.title == "Tajemnice historii"
```

Tryb live wymaga w `.env`: `EXTERNAL_PROVIDERS_MODE=live`, `OPENAI_API_KEY`
i `OPENAI_MODEL` obsługującego Structured Outputs. Klucz jest `SecretStr`.
Brak klucza/modelu zatrzymuje start z czytelnym komunikatem. Compose przekazuje
te ustawienia do API. Wybór modelu i jego ceny pozostają jawne.

`OPENAI_TIMEOUT_SECONDS=30` ustawia timeout operacji HTTP w SDK (nie całego
workflow). `OPENAI_MAX_RETRIES=2` oznacza maksymalnie 3 próby; dopuszczalne 0–3.
SDK stosuje exponential backoff z jitterem i respektuje Retry-After; ponawia
m.in. problemy połączenia, timeout, 408, 409, 429 i 5xx. Nie ma dodatkowej pętli
ponowień w aplikacji. Nie ponawiamy walidacji, odmowy ani niepełnej odpowiedzi.
`LLMUnavailable`, `LLMInvalidOutput`, `LLMRefusal` i `LLMIncomplete` przekazują
bezpieczne komunikaty bez treści promptów, kluczy i odpowiedzi dostawcy.

Wynik zawiera nazwę providera/modelu, response ID i dostępne statystyki tokenów.
Brak usage jest oznaczony `None`; mock raportuje zero. Nie są to wyliczone koszty
USD ani trwały rejestr kosztów — to zakres późniejszego etapu Cost Tracking.

Testy SDK korzystają z prawdziwego parsera i `httpx.MockTransport`; nie wymagają
klucza ani dostępu do OpenAI: `uv run pytest tests/unit/test_llm.py -q`.
Channel Intelligence Service opisano poniżej.


## Etap 4 — Channel Intelligence

Po utworzeniu kanału uruchom analizę jawnie, przekazując JWT właściciela:

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/channels/$CHANNEL_ID/analyze" \
  -H "Authorization: Bearer $TOKEN"
```

Endpoint nie przyjmuje body. Zwraca `200` i pełny `ChannelDetail` z zapisanym
blueprintem. Używa `Channel.idea` oraz `Channel.language`; nie zmienia języka,
budżetu, częstotliwości publikacji, statusu ani trybu autopilota kanału.
Utworzenie kanału nadal nie wykonuje płatnego wywołania — analiza jest osobną akcją.

Blueprint zawiera:

- `niche_description`, `target_audience`, `tone`;
- `formats` (TOP5/STORY, suma wag 1) i 1–20 unikalnych `content_pillars`;
- `video_style` (długość, tempo, limit hooka) i `hook_style`;
- `visual_style` (opis i udział scen video);
- `suggested_posting_strategy` (proponowane `videos_per_day` i uzasadnienie);
- `seed_keywords` (1–20 fraz do przyszłego researchu).

Nowe pola konfiguracji są przechowywane w istniejącym JSON blueprintu.
Stare rekordy odczytują się z pustymi wartościami domyślnymi — migracja tabel
nie jest potrzebna. PATCH blueprintu obsługuje także nowe pola; nadal zastępuje
całą konfigurację. Propozycja publikacji nie nadpisuje faktycznego harmonogramu.

W `mock` otrzymasz oznaczoną przykładową strategię offline w języku kanału,
a nie analizę rynku. W `live` serwis korzysta z OpenAI przez `LLMProvider`.
Schemat odpowiedzi wymaga wszystkich pól i waliduje je przed zapisem.
Brak realnego web search: strategia jest propozycją modelu, a nie zweryfikowanym
researchem konkurencji ani prognozą popularności.

Zapis konfiguracji i zastąpienie filarów stanowią jedną transakcję.
Podczas wywołania LLM nie utrzymujemy blokady ani transakcji DB. Przed zapisem
ponownie sprawdzamy właściciela i wersję (`updated_at`) z blokadą wiersza.
Jeśli w międzyczasie zmienił się kanał/blueprint, zwracamy `409`; wynik nie
nadpisuje zmian. Ponowna świadoma analiza zastępuje blueprint, nie dopisuje filarów.
Każde wywołanie w trybie live może generować koszt; endpoint nie jest cache'owany.

Błędy: `401` brak uwierzytelnienia, `404` kanał nie istnieje lub jest cudzy,
`409` równoczesna zmiana, `422` odmowa modelu, `502` błędna/niepełna odpowiedź,
`503` niedostępny provider lub baza. Nieudana analiza zachowuje poprzedni blueprint.
HTTP/logi nie ujawniają treści promptów, odpowiedzi dostawcy ani sekretów.

`CompetitorResearchProvider` w `app/modules/intelligence/research.py` definiuje
zapytanie (język, słowa kluczowe, limit) i zwalidowany wynik. Lokalna implementacja
filtruje jawnie przekazane rekordy, domyślnie zwraca pustą listę. Nie wykonuje sieci
ani nie wymyśla prawdziwych konkurentów. Przyszły adapter wyszukiwarki implementuje
`research(query)`; zapisywanie wyników i endpointy konkurencji opisano w etapie 5.

Analiza na tym etapie jest synchronicznym endpointem `def` w threadpool FastAPI.
Kolejki i trwałe joby pozostają na etap 14, rejestr kosztów na etap 19.

## Etap 5 — Competitor Research

Endpointy wymagają JWT właściciela kanału (cudzy kanał zwraca 404):

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/channels/$CHANNEL_ID/competitor-research" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body \
  "http://localhost:8000/api/v1/channels/$CHANNEL_ID/competitors?limit=20&offset=0" \
  -H "Authorization: Bearer $TOKEN"
```

POST nie przyjmuje body. Korzysta z języka kanału i seed keywords blueprintu;
brak słów kluczowych zwraca 409 (najpierw analiza lub ręczny PATCH blueprintu).
Zwraca `{"processed": N, "items": [...]}`. GET zwraca `items`, `total`, `limit`,
`offset`; limit 1–100, offset >= 0, kolejność po nazwie i ID.

`Competitor` przechowuje nazwę, platformę, URL, język, niszę, obserwowane formaty,
typową długość, częstotliwość publikacji, notatki i czas aktualizacji.
`CompetitorContent` przechowuje przykładowe tytuły i ich kolejność. To materiały
benchmarkowe; nie pobieramy ani nie kopiujemy scenariuszy, filmów lub transkrypcji.

Migracja `0004` dodaje tabele SQLModel, indeksy, unikalność URL w obrębie kanału
oraz kaskady usuwania. Compose stosuje migrację przy starcie API; lokalnie:
`uv run --no-active alembic upgrade head`.

Powtórny research aktualizuje wpisy według dokładnego, znormalizowanego przez
Pydantic URL (nie rozpoznaje aliasów adresów tej samej platformy). Zastępuje ich
listę przykładowych tytułów, usuwając identyczne powtórzenia. Zachowuje ID konkurenta.
Nie usuwa konkurentów nieobecnych w nowym wyniku; pusty wynik zachowuje historię.
Cała paczka jest walidowana i zapisywana atomowo. Konflikt zmiany kanału podczas
researchu zwraca 409, błędne wyniki 502, niedostępny provider/baza 503.

Domyślnie `get_competitor_research_provider` zwraca pusty lokalny provider zarówno
w mock, jak i live. **Wyszukiwarka nie jest jeszcze podłączona.** Można wstrzyknąć
`LocalCompetitorResearchProvider(records)` z rekordami `CompetitorResearchResult`
albo własny adapter implementujący `research(query)`. Testy pokazują wstrzyknięcie
przez `app.dependency_overrides[get_competitor_research_provider]`. Przyszły adapter
powinien mapować błędy połączenia na `ResearchUnavailable`, mieć timeout i ograniczone
ponowienia. Nie używamy LLM do wymyślania konkurentów ani nie wykonujemy płatnych calli.

## Etap 6 — Idea Engine

Wszystkie endpointy poniżej wymagają JWT właściciela kanału:

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/channels/$CHANNEL_ID/ideas/generate?count=10" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body \
  "http://localhost:8000/api/v1/channels/$CHANNEL_ID/ideas?status=candidate&limit=20&offset=0" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/ideas/$IDEA_ID/approve" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/ideas/$IDEA_ID/reject" \
  -H "Authorization: Bearer $TOKEN"
```

Generowanie nie wymaga body; `count` jest liczbą 10–20, domyślnie 10.
Zwraca `201 {"items": [...]}`. Wymaga blueprintu z odbiorcami i przynajmniej jednym
filarem (analiza kanału albo ręczna konfiguracja); w przeciwnym razie 409.
Lista zwraca `items`, `total`, `limit`, `offset`, od najnowszych. Opcjonalny filtr
`status`: candidate, approved, rejected, used. Limit 1–100, offset >= 0.

Każdy pomysł zawiera tytuł, koncept, nazwę filaru, format TOP5/STORY, pomysł na hook,
uzasadnienie i dwa pola `novelty_heuristic` / `visual_potential_heuristic` z `score`
0–1 oraz `rationale`. To subiektywne heurystyki, nie prognoza popularności.
TOP5 jest tematem do późniejszego researchu, a STORY fikcją — nie generujemy tu
zweryfikowanych faktów ani scenariuszy.

Generator otrzymuje blueprint, filary, język, do 20 benchmarków konkurentów i do
100 ostatnich tematów ze wszystkich statusów. Liczba rekordów historycznych też
trafia do kontekstu. Dłuższy kontekst jest ograniczany przez usuwanie najstarszych
tematów, potem benchmarków; blueprint pozostaje kompletny. Historia tematów jest
zatem kontekstem ograniczonym, nie pełnym systemem wykrywania podobieństwa.

Walidacja odrzuca całą paczkę przy niepoprawnej liczbie, nieznanym filarze, formacie
z zerową wagą, błędnej heurystyce, powtarzającym się tytule lub tytule identycznym
z benchmarkiem przekazanym do generatora. Tytuły normalizujemy Unicode NFKC,
casefold i białe znaki. Kontrola tytułów wobec wszystkich zapisanych pomysłów
obejmuje też starsze rekordy poza kontekstem promptu. Nie wykrywa parafraz.
Format mix jest wskazówką proporcji; przy małych paczkach proporcje są przybliżone.

Nowe pomysły mają status `candidate`. Approve/reject zwracają `200` z pomysłem;
ponowienie tej samej decyzji nie zmienia timestampu. Można zmienić decyzję między
approved i rejected do czasu `used`. Status used jest nadawany przy tworzeniu
video w etapie 7; oba endpointy decyzji odrzucają wtedy zmianę z 409.

Nazwa filaru jest snapshotem: ponowna analiza kanału nie usuwa pomysłów.
Zapis całej paczki jest atomowy i sprawdza zmianę kanału po wywołaniu LLM.
Konflikt daje 409; błędy LLM 422/502/503, brak lub cudzy zasób 404.
Tryb mock daje jawne, deterministyczne przykładowe pomysły offline. Live korzysta
z istniejącego OpenAIProvider; każde generowanie jest nową, potencjalnie płatną akcją.

Migracja `0005` tworzy `content_ideas`, ograniczenia statusów/formatów i indeksy.
Compose stosuje ją przy starcie. Lokalnie: `uv run --no-active alembic upgrade head`.

## Etap 7 — domena Video

Utwórz film z **zatwierdzonego** pomysłu, używając JWT właściciela:

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/ideas/$IDEA_ID/create-video" \
  -H "Authorization: Bearer $TOKEN"
```

Endpoint nie wymaga body. Pierwsze wywołanie zwraca `201` i Video z `id`, `idea_id`,
`status`, formatem, językiem, docelową długością, budżetem i snapshotem blueprintu.
Status po starcie to `IDEA_GENERATED`. Pomysł atomowo przechodzi do `used`.
Candidate/rejected (lub used bez istniejącego filmu) dają 409. Brak JWT daje 401,
nieistniejący/cudzy pomysł 404. Admin również musi być właścicielem.

Jeden pomysł może utworzyć tylko jeden film. Ponowienie zwraca `200` z tym samym
Video i jego aktualnym statusem — nie resetuje workflow ani historii. Dotyczy to
również równoczesnych żądań. Nie trzeba przesyłać osobnego klucza idempotencji.
Budżet, długość i blueprint są snapshotem chwili utworzenia; tytuł, język i format
pochodzą z pomysłu. Późniejsza edycja kanału nie zmienia istniejącego filmu.

Maszyna stanów:

```text
DRAFT → IDEA_GENERATED
  TOP5: → RESEARCHING → RESEARCHED → SCRIPTING
  STORY: → SCRIPTING
→ SCRIPT_READY → GENERATING_ASSETS → ASSETS_READY
→ GENERATING_AUDIO → READY_TO_RENDER → RENDERING
→ QUALITY_CHECK → READY → PUBLISHED
```

Z każdego stanu poza FAILED/PUBLISHED można przejść do FAILED. FAILED i PUBLISHED
są na razie końcowe; recovery/retry będą projektowane z jobami w kolejnych etapach.
Powtórzenie bieżącego stanu jest no-op. Nie ma publicznego endpointu dowolnej
zmiany statusu. Wewnętrzny `transition_video` blokuje rekord filmu, waliduje przejście
oraz zapisuje status i `VideoStatusEvent` w transakcji zarządzanej przez wywołującego.
Opcjonalny `expected_status` odrzuca przejście, gdy oczekiwany stan jest nieaktualny.
Powód przejścia ma być krótkim, bezpiecznym opisem/kodem, bez surowych błędów providera.

Przy tworzeniu zapisujemy zdarzenia `null → DRAFT` i `DRAFT → IDEA_GENERATED`.
Historia ma rosnącą sekwencję, poprzedni/nowy status, powód i timestamp.
Kolejny etap workflow jeszcze nie jest wykonywany: **nie powstaje gotowy film,
scenariusz ani sceny**. W etapie 8 powstanie generator scenariuszy STORY, w etapie 9
research TOP5, a w etapie 14 worker. Start workflow na tym etapie jest trwałym
przygotowaniem Video w stanie IDEA_GENERATED, bez uruchamiania pustego zadania.

Dodano SQLModel `VideoScript` (jeden na film) i `Scene` (kolejne pozycje w skrypcie)
oraz wejściowe schematy Pydantic. Scena ma czas trwania, narrację, prompt, typ
image/video/stock/none, ruch kamery, nastrój i wyróżnienia napisów. Walidacja sumy
czasów oraz generowanie/zapis scenariusza należą do etapu 8. Nie tworzymy pustych
skryptów przy POST create-video. Encje video są połączone z kanałem przez ContentIdea.
Usunięcie kanału kaskadowo usuwa pomysły, filmy, historię, skrypty i sceny.

Migracja `0006` dodaje tabele, enumy, unikalności i ograniczenia DB. Compose wykonuje
ją przy starcie; lokalnie: `uv run --no-active alembic upgrade head`.

## Etap 8 — STORY Script Engine

Po zatwierdzeniu pomysłu STORY i utworzeniu Video uruchom jawnie generowanie:

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/script/generate" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/script" \
  -H "Authorization: Bearer $TOKEN"
```

Oba endpointy wymagają JWT właściciela i zwracają `200` ze skryptem. Cudzy lub
nieistniejący film/skrypt daje 404, brak JWT 401. POST nie wymaga body. Działa dla
STORY w IDEA_GENERATED; TOP5, trwające generowanie i FAILED dają 409.
Jeżeli skrypt już istnieje, ponowny POST zwraca go bez wywołań LLM i bez zmiany stanu.

Pipeline wykonuje trzy wywołania LLMProvider:

1. `StoryOutline`: zarys hook → setup → escalation → reveal → twist.
2. `StoryNarrative`: pełna narracja tych pięciu części na podstawie zarysu.
3. `StoryScenes`: podział narracji na 5–50 scen, prompty wizualne i czasy.

Kontekst obejmuje koncept/hook pomysłu, tytuł, język, docelową długość i zapisany
snapshot blueprintu Video. Każda odpowiedź jest walidowana przez Pydantic.
Suma duration musi mieścić się w ±10% duration_target, pozycje scen zaczynają się
od 1 i są kolejne. Hook jest pierwszą sceną i mieści się w hook_max_seconds.
Tytuł, język i duration_target nie mogą zmienić się w odpowiedzi modelu.
Narracja scen musi odtwarzać całą historię w kolejności; porównanie ignoruje tylko
białe znaki. Puste prompty i brakujące części są odrzucane. Czas jest szacunkiem
scenariusza; faktyczny czas audio będzie znany dopiero przy TTS.

Stan SCRIPTING jest zapisywany przed wywołaniem providera, co blokuje drugi kosztowny
request. Wywołania zewnętrzne nie trzymają transakcji ani blokady bazy. Zarys,
historia, VideoScript, Scene i przejście do SCRIPT_READY zapisują się atomowo.
Błąd providera/walidacji/zapisu usuwa częściowe efekty transakcji i zapisuje FAILED
z bezpiecznym kodem w historii. Błędy modelu zwracają 422/502/503 zależnie od rodzaju.
Przy niedostępnej bazie zapis FAILED może się nie udać; log rejestruje ten przypadek.

To nadal synchroniczny endpoint w threadpool. Awaria procesu po zapisie SCRIPTING
może pozostawić film w tym stanie; nie uruchamiamy automatycznie kolejnych płatnych
prób. Trwałe joby, wykrywanie przerwanego zadania i recovery należą do etapu 14.
FAILED pozostaje stanem końcowym zgodnie z etapem 7; endpoint retry nie został dodany.

Mock zawiera jawną przykładową fikcyjną historię PL/EN i deterministyczne sceny.
Nie interpretuje kreatywnie każdej niszy; służy do testowania przepływu offline.
Live korzysta z OpenAIProvider i jego timeout/retries. Trzy kroki mogą oznaczać trzy
płatne wywołania (plus ograniczone ponowienia SDK). Nie wykonujemy dodatkowych
ponowień po błędach walidacji. Director z etapu 10 dobierze docelowe typy wizualne;
mock używa obrazów. Render i audio nie są jeszcze generowane.

Migracja `0007` dodaje do video_scripts JSON `outline` i `story`, zachowując stare
rekordy z pustymi obiektami. Istniejące etapy nie tworzyły skryptów przez API;
puste rekordy wprowadzone ręcznie nie stanowią prawidłowego scenariusza STORY.
Compose stosuje migrację przy starcie. Lokalnie: `uv run --no-active alembic upgrade head`.

## Etap 9 — TOP5 Research Engine

Po utworzeniu Video z zatwierdzonego pomysłu TOP5:

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/research" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/research" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/top5-script/generate" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/script" \
  -H "Authorization: Bearer $TOKEN"
```

Endpointy wymagają JWT właściciela; cudzy/nieistniejący film daje 404. POST-y nie
przyjmują body, sukces daje 200. Research działa tylko dla TOP5 w IDEA_GENERATED,
a scenariusz dopiero po RESEARCHED. Niewłaściwy format/stan lub równoległy request
daje 409. Brak danych daje 422 i FAILED; nie generujemy wtedy scenariusza.
Ponowienie ukończonego researchu w RESEARCHED/SCRIPT_READY oraz gotowego skryptu
zwraca zapisany wynik bez ponownego wywołania providerów.

Pipeline:

1. LLM tworzy 1–5 zapytań na podstawie tematu, konceptu i języka.
2. ResearchProvider dostarcza dokumenty; zapisujemy maksymalnie 6 unikalnych URL,
   każdy do 8000 znaków, w języku filmu.
3. LLM wybiera do 30 cytowanych stwierdzeń i identyfikatory dokumentów. Serwer
   odrzuca nieznane ID i cytaty, które nie występują dosłownie w danym dokumencie.
4. Fakty z confidence >= 0.7 są deduplikowane po treści. Warunek kontynuacji:
   minimum 5 różnych faktów z co najmniej 2 dokumentów o różnych URL.
5. LLM wybiera kolejność pięciu zapisanych faktów, czasy i prompty. Narracja każdej
   sceny jest wstawiana przez serwer z ResearchFact.statement — model nie może
   dodać twierdzeń z pamięci. Pierwsza scena to neutralne „Pięć faktów.” / „Five facts.”.

Kolejność jest redakcyjna, nie obiektywnym rankingiem. Czasy scen mają sumować się
w tolerancji ±10% celu. Skrypt ma sześć scen (hook + pięć faktów) oraz `citations`,
które łączą pozycję sceny z pełnym faktem i źródłem. GET /script obsługuje teraz
STORY i TOP5. Endpoint /script/generate pozostaje generatorem STORY.

ResearchDocument zapisuje URL, tytuł, fragment dokumentu, metadane i czas pobrania.
ResearchFact zapisuje statement, source_url, source_title, confidence i metadata_json
(cytat i zapytania). SceneResearchFact utrwala powiązanie sceny z faktem.
Niewystarczający research z poprawnymi dokumentami/faktami zostaje zachowany do
inspekcji przez GET /research, ale film przechodzi do FAILED. Nieprawidłowe wyniki
nie są zapisywane jako fakty. Błąd zapisu skryptu/cytowań wycofuje skrypt i sceny,
zachowując wcześniejszy research, i ustawia FAILED z bezpiecznym kodem.

**Domyślny LocalResearchProvider jest pusty, także w live.** Nie ma jeszcze adaptera
wyszukiwarki ani pobierania stron. Domyślny POST /research zakończy się więc 422,
zamiast wymyślać źródła. Aby dostarczyć własny, jawny korpus, wstrzyknij
`LocalResearchProvider(list[SourceDocument])` przez zależność `get_research_provider`.
Przykład integracji i dane testowe są w tests/conftest.py oraz test_top5_research.py.
Przyszły adapter implementuje `search(ResearchQuery)` i powinien zapewniać timeout,
ograniczone retries i mapować niedostępność na ResearchUnavailable. URL nie jest
pobierany przez serwis domenowy. Mock LLM wybiera linie z przekazanego korpusu;
nie tworzy fikcyjnych dokumentów ani adresów.

Confidence jest heurystyką dopasowania/wsparcia, nie dowodem prawdziwości.
Weryfikujemy pochodzenie cytatu, lecz nie wiarygodność wydawcy, kompletność kontekstu
ani semantyczną prawdziwość zdania. Dwa różne URL nie gwarantują niezależności źródeł.
Ta wersja używa cytatów zamiast swobodnych parafraz, aby mechanicznie ograniczyć
narrację do dostarczonych danych. Dobór zaufanego korpusu pozostaje odpowiedzialnością
adaptera/operatora. Nie jest to automatyczny fact-checking internetu.

Przejścia: IDEA_GENERATED → RESEARCHING → RESEARCHED → SCRIPTING → SCRIPT_READY.
Claim statusu jest trwały przed zewnętrznym wywołaniem; nie trzymamy wtedy blokad DB.
Tak jak STORY, przerwanie procesu może wymagać recovery planowanego w etapie 14.
Nie ma automatycznego retry FAILED. Migracja `0008` dodaje encje i kaskady; Compose
wykonuje ją przy starcie. Lokalnie: `uv run --no-active alembic upgrade head`.

## Etap 10 — Director

Po uzyskaniu SCRIPT_READY (STORY lub TOP5):

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/direct" \
  -H "Authorization: Bearer $TOKEN"
curl --fail-with-body \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/direction" \
  -H "Authorization: Bearer $TOKEN"
```

Oba endpointy wymagają JWT właściciela. Zwracają `200` z DirectorPlan i listą scen.
POST nie wymaga body. Brak planu daje 404, niewłaściwy status/niepełne sceny lub
zbyt mały budżet daje 409. Cudzy film daje 404. Ponowienie POST zwraca istniejący
plan bez ponownego dopisywania stylu do promptów. Nie ma jeszcze replanowania.

DirectorService działa deterministycznie, bez dodatkowego LLM i płatnych wywołań.
Czyta snapshot ChannelBlueprint zapisany w Video, budget_limit_usd i długości scen:

- `visual_prompt`: istniejący prompt (lub narracja, gdy pusty) ze stylem blueprintu;
- `visual_style`: opis z blueprintu;
- `importance`: hook 1.0, zakończenie 0.9, pozostałe 0.4 + 0.4 × udział czasu sceny;
- `generation_priority`: 1 oznacza pierwszą scenę do generowania, kolejność po importance,
  a przy remisie po pozycji;
- `visual_type`: image domyślnie, video dla najważniejszych scen mieszczących się w limicie;
- `camera_motion`: zoom_in dla obrazu, static dla video (ruch może być już w klipie).

To prosta heurystyka znaczenia scen, nie semantyczna ocena ani prognoza popularności.
Narracja, czasy, kolejność scen i powiązania faktów TOP5 pozostają zachowane.
Liczba scen video nie przekroczy floor(liczba_scen × video_scene_ratio) z blueprintu;
przy małej liczbie scen rzeczywisty udział może być niższy. Domyślne .25 i 5 scen
dają 1 video / 4 obrazy. Operator może ustawić inny udział w blueprintcie przed
utworzeniem Video. Późniejsze zmiany kanału nie modyfikują snapshotu filmu.

Planowanie budżetu korzysta z interfejsu VisualCostEstimator i konfigurowalnych
**przykładowych stawek szacunkowych**, nie aktualnego cennika dostawcy:

| Zmienna .env | Domyślnie | Znaczenie |
| --- | --- | --- |
| DIRECTOR_IMAGE_ESTIMATE_USD | 0.005 | szacunek jednego obrazu |
| DIRECTOR_VIDEO_SECOND_ESTIMATE_USD | 0.01 | szacunek sekundy video |
| DIRECTOR_VISUAL_BUDGET_FRACTION | 0.5 | część budżetu Video przeznaczona na wizualizacje |

Te same wartości przekazuje Compose. Część wizualna jest zaokrąglana w dół do
6 miejsc dziesiętnych. Najpierw rezerwujemy szacunkowy koszt samych obrazów.
Jeśli nie mieści się w części wizualnej, cały plan jest odrzucany (409).
Następnie w kolejności importance zamieniamy obrazy na video tylko wtedy, gdy
zmiana kosztu duration × stawka_video - stawka_obrazu mieści się w limicie.
Gdy droższa scena się nie mieści, sprawdzamy kolejne. Nie optymalizujemy globalnie
liczby klipów kosztem ich priorytetu. Gdy budżet wystarcza tylko na obrazy,
plan pozostaje image-only. Plan zapisuje zastosowane stawki i szacunkowy koszt.

Nie jest to rachunek za generowanie, gwarancja kosztu całego filmu ani rezerwacja
środków u dostawcy. Nie uwzględniamy jeszcze rzeczywistych kosztów LLM/TTS, minimalnej
długości klipu danego dostawcy i jego zasad rozliczeń. Adapter pricing oraz pełne
CostEvent/BudgetService będą rozwijane przy integracjach i w etapie 19.

Plan i aktualizacje wszystkich scen zapisują się atomowo pod blokadą Video.
Błąd wycofuje zmiany i pozwala ponowić planowanie. Status pozostaje SCRIPT_READY:
nie uruchamiamy jeszcze generowania assetów. Migracja `0009` dodaje DirectorPlan
oraz pola visual_style, importance, generation_priority do Scene. Dla wcześniejszych
scen importance/priority pozostają null, dopóki Director nie utworzy planu.
Compose stosuje migrację przy starcie; lokalnie `uv run --no-active alembic upgrade head`.

## Etap 11 — Asset Domain i storage

Migracja `0010` dodaje modele SQLModel:

- `Asset`: typ `image`, `video`, `audio`, `subtitle`, `final_video`; film,
  opcjonalna scena i zadanie, backend/bucket/klucz obiektu, MIME, rozmiar,
  SHA-256, metadane JSON i czas utworzenia. Plik nie trafia do PostgreSQL.
- `GenerationJob`: scena, provider, typ, parametry JSON, status
  `pending/running/succeeded/failed`, licznik ponowień, bezpieczny opis błędu,
  czas rozpoczęcia/zakończenia i klucz idempotencji.

Klucz idempotencji jest unikalny w scenie. Jedno zadanie może mieć jeden wynik
Asset; ponowienie powinno użyć tego samego zadania i klucza obiektu. Osobna
regeneracja otrzyma nowy klucz idempotencji. DB sprawdza statusy, czasy zadań,
nieujemne retry_count, dodatni rozmiar i unikalną lokalizację obiektu.

`StorageProvider` w `app/shared/storage.py` ma `put()`, `get_url()`, `delete()`
i `close()`. Implementacje znajdują się w `app/integrations/storage/`.
`put()` przyjmuje binarny strumień od jego bieżącej pozycji i zwraca model Pydantic
`StoredObject` z rozmiarem i SHA-256. Ponowny zapis tego samego klucza zastępuje
obiekt; `delete()` jest idempotentne. Maksymalny rozmiar to domyślnie 100 MiB.
Puste pliki i niepoprawne klucze są odrzucane.

### Wybór storage

| Zmienna | Domyślnie / znaczenie |
| --- | --- |
| `STORAGE_BACKEND` | `local` poza Compose; Compose ustawia `s3` |
| `STORAGE_LOCAL_ROOT` | `AI-Slop_backend/data/assets`, prywatny katalog |
| `STORAGE_MAX_BYTES` | `104857600` |
| `S3_ENDPOINT_URL` | lokalnie `http://localhost:8333`, w Compose `http://seaweedfs:8333` |
| `S3_PUBLIC_ENDPOINT_URL` | adres do podpisanych URL; poza Compose domyślnie taki jak endpoint |
| `S3_BUCKET` | `ai-slop` |
| `S3_REGION` | `us-east-1` |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | sekrety przez pydantic-settings; wymagane dla S3 |
| `S3_URL_SECONDS` | 300, maksymalnie 3600 |
| `S3_TIMEOUT_SECONDS` | 10 na połączenie i odczyt; maks. 3 próby SDK |
| `S3_PORT` | 8333, port hosta dla Compose |

Uruchomienie z SeaweedFS:

```sh
docker compose up -d --build
```

Compose uruchamia SeaweedFS 4.47 w trybie `mini`, tworzy bucket i przechowuje
obiekty w volume `seaweedfs_data`. Port S3 jest wystawiony tylko na localhost.
Domyślne poświadczenia deweloperskie są w `.env.example`; przy wdrożeniu ustaw
własne. Poświadczenia SeaweedFS inicjalizują się przy pierwszym uruchomieniu;
zmiana `.env` nie zastępuje automatycznie konfiguracji istniejącego volume.
Zewnętrzny bucket S3 trzeba przygotować przed użyciem — adapter nie tworzy go
ani nie zmienia jego uprawnień.
[Dokumentacja SeaweedFS mini](https://github.com/seaweedfs/seaweedfs/wiki/Quick-Start-with-weed-mini).

Lokalnie bez Dockera storage działa na plikach. Zastosuj migrację:

```sh
uv run --no-active alembic upgrade head
```

Przykład wewnętrznego użycia (nie endpoint HTTP):

```python
from io import BytesIO
from uuid import uuid4

from app.core.config import get_settings
from app.integrations.storage.factory import create_storage_provider

storage = create_storage_provider(get_settings())
try:
    key = f"demo/{uuid4()}/example.txt"
    result = storage.put(key, BytesIO(b"example"), content_type="text/plain")
    location = storage.get_url(result.key)
    storage.delete(result.key)
finally:
    storage.close()
```

FastAPI udostępnia adapter wewnętrznym usługom przez `app.state.storage` i zamyka
go przy zatrzymaniu. Wariant lokalny zwraca `file://` dla procesu backendu,
nie publiczny URL dla przeglądarki. Nie montujemy katalogu jako static files.
Zapis lokalny używa pliku tymczasowego i atomowej podmiany; odrzuca `..`, ścieżki
absolutne i symlinki. Katalog musi być zapisywalny wyłącznie przez zaufany proces.

S3 zwraca podpisany URL ważny przez `S3_URL_SECONDS`. Każdy posiadacz tego URL
może pobrać obiekt do wygaśnięcia, dlatego nie zapisujemy URL w DB ani logach.
Podpis powstaje od razu z publicznym adresem; nie wolno później podmieniać hosta.
Adapter używa SigV4, path-style i ograniczonych retries SDK.
[Dokumentacja podpisanych URL](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/generate_presigned_url.html).

### Zakres i dalsza integracja

Ten etap dostarcza modele oraz adaptery; nie dodaje jeszcze endpointów uploadu,
generowania mediów ani wykonawcy GenerationJob. Istniejące zasady JWT pozostają
w użyciu, a żaden nowy publiczny endpoint nie udostępnia plików. Przyszła usługa
musi sprawdzić właściciela filmu oraz zgodność sceny/zadania z filmem przed
utworzeniem Asset lub wydaniem URL. Tych relacji nie należy przyjmować z klienta
bez walidacji. Parametry joba nie mogą zawierać sekretów, a error surowych odpowiedzi SDK.

Zapis obiektu i transakcja PostgreSQL nie są jedną transakcją. Integracja generowania
musi używać stabilnego klucza obiektu i obsłużyć rollback/recovery. Kaskadowe
usunięcie metadanych filmu nie usuwa plików z bucketa; sprzątanie osieroconych
obiektów zostanie podłączone wraz z workflow. Sam model joba nie wykonuje retries
ani nie wznawia pracy po restarcie — to zadania przyszłych workerów.

### Testy storage

`uv run --no-active pytest` uruchamia testy lokalne i testy SDK bez sieci.
Testy integracyjne wymagają dodatkowo `TEST_DATABASE_URL`, `TEST_REDIS_URL` oraz:

```sh
export TEST_S3_ENDPOINT_URL=http://localhost:8333
export TEST_S3_BUCKET=ai-slop
export TEST_S3_ACCESS_KEY_ID=ai-slop-local
export TEST_S3_SECRET_ACCESS_KEY=ai-slop-local-secret
uv run --no-active pytest -ra
```

Używaj środowiska testowego. Test S3 zapisuje i usuwa wyłącznie losowy własny klucz
`integration/<uuid>/image.png`. GitHub Actions uruchamia własny SeaweedFS obok
PostgreSQL i Redis, dzięki czemu ten test jest wykonywany przy push i PR.

## Etap 12 — interfejs generowania i Runpod

`app/shared/generation.py` definiuje `ImageGenerationProvider` oraz
`VideoGenerationProvider`. Metody `generate_image()` / `generate_video()` zwracają
referencję zleconego zadania i bieżący status. `get_status()` odczytuje wynik,
`cancel()` wysyła żądanie anulowania, a `close()` zwalnia klienta HTTP.
Są to wewnętrzne interfejsy Pythona, bez nowych publicznych endpointów HTTP.

Wejście i wynik są modelami Pydantic. Request zawiera `request_id`, prompt,
rozdzielczość, opcjonalny seed i słownik parametrów konkretnego workera; video
wymaga dodatkowo `duration_seconds`. Referencja `GenerationJobRef` zawiera provider,
endpoint_id i job_id. Zachowanie endpoint_id pozwala odpytać starsze zadanie po
zmianie domyślnego endpointu w `.env`. Referencje pochodzą z zaufanego backendu,
nie należy przyjmować ich bezpośrednio od użytkownika bez kontroli właściciela.

### Mock

`EXTERNAL_PROVIDERS_MODE=mock` wybiera `MockRunpodProvider`. Kolejne odczyty
przeprowadzają zadanie przez queued → running → succeeded. Anulowanie zadania
oczekującego lub pracującego daje cancelled; gotowy wynik pozostaje gotowy.
Ten sam request_id i dane zwracają to samo zadanie, a zmienione dane pod tym samym
ID są odrzucane. Pamięć mocka jest lokalna dla procesu i znika przy jego zamknięciu.
Mock zwraca JSON z `mock: true`; nie produkuje jeszcze plików PNG/MP4.

Przykład bez sieci i płatnych wywołań:

```python
from app.integrations.runpod.mock import MockRunpodProvider
from app.shared.generation import ImageGenerationRequest

provider = MockRunpodProvider()
try:
    submitted = provider.generate_image(
        ImageGenerationRequest(request_id="demo-scene-1", prompt="A misty forest")
    )
    running = provider.get_status(submitted.job)
    completed = provider.get_status(submitted.job)
    assert completed.output["mock"] is True
finally:
    provider.close()
```

FastAPI tworzy wybrany adapter w lifespan, udostępnia go jako
`app.state.generation` / zależność `CurrentGeneration` i zamyka podczas shutdown.
Start aplikacji sam nie wysyła żadnego zadania.

### Live

`EXTERNAL_PROVIDERS_MODE=live` wybiera `RunpodProvider` korzystający z httpx.
Ustaw w `.env` (Compose przekazuje te zmienne):

| Zmienna | Znaczenie / domyślnie |
| --- | --- |
| `RUNPOD_API_KEY` | SecretStr, wymagany przy operacji live |
| `RUNPOD_IMAGE_ENDPOINT_ID` | ID własnego endpointu obrazów |
| `RUNPOD_VIDEO_ENDPOINT_ID` | ID własnego endpointu wideo |
| `RUNPOD_IMAGE_MODEL` | opcjonalny identyfikator modelu przekazywany workerowi |
| `RUNPOD_VIDEO_MODEL` | opcjonalny identyfikator modelu przekazywany workerowi |
| `RUNPOD_TIMEOUT_SECONDS` | timeout operacji HTTP, 20 s |
| `RUNPOD_STATUS_MAX_RETRIES` | ponowienia odczytu statusu, 2 (maks. 3) |
| `RUNPOD_EXECUTION_TIMEOUT_MS` | limit wykonania u providera, 600000 ms |
| `RUNPOD_JOB_TTL_MS` | czas życia zadania, 3600000 ms; nie mniejszy niż execution timeout |

Globalny tryb live dotyczy też istniejącego adaptera OpenAI, więc jego wcześniejsze
wymagania konfiguracyjne nadal obowiązują. Brak konfiguracji Runpod jest zgłaszany
przy użyciu adaptera i nie blokuje samego logowania czy analizy kanału.

Adapter używa kolejki Runpod: POST `/run`, GET `/status/{id}` i POST `/cancel/{id}`
pod `https://api.runpod.ai/v2/{endpoint_id}`. Anulowanie potwierdza przyjęcie żądania;
końcowy status należy odczytać osobno. Endpoint musi być typu queue-based.
Kontrakt workera zależy od wdrożonego handlera; backend wysyła nasze pola jako
`input` i limity jako `policy`. Worker musi rozumieć `type`, `request_id`, `prompt`,
`width`, `height`, `parameters`, opcjonalne `seed`/`model` i `duration_seconds` dla video.
Nie zakładamy konkretnego FLUX, Wan ani formatu odpowiedzi gotowego szablonu.
[Dokumentacja Runpod](https://docs.runpod.io/serverless/endpoints/send-requests).

Statusy zewnętrzne są mapowane na enum:
queued, running, succeeded, failed, cancelled, timed_out. Wynik `output` pozostaje
JSON-em specyficznym dla workera; ukończone zadanie musi go zawierać. Adapter nie
pobiera adresów zwróconych przez workera. Przyszła integracja Asset musi zweryfikować
format wyniku i bezpiecznie skopiować media do własnego storage. Limit odpowiedzi
adaptera wynosi 1 MiB, a requestu 256 KiB — duże media powinny być w object storage.
[Operacje Runpod](https://docs.runpod.io/serverless/endpoints/operation-reference).

### Błędy i ponowienia

Odczyt statusu ponawia błędy transportu, 429 i 5xx z ograniczonym exponential
backoff. POST nie jest automatycznie ponawiany. Timeout, 5xx lub niepoprawna
odpowiedź przy wysłaniu zadania daje `GenerationSubmissionUnknown`: zewnętrzny
job mógł już powstać. Nie należy wtedy automatycznie wysyłać nowego `/run`.
`request_id` służy korelacji z workerem; nie daje gwarancji deduplikacji przez API
Runpod. Worker może implementować własną deduplikację na tym kluczu.

Brak endpointu/joba, odrzucenie requestu, chwilowa niedostępność i niepoprawny wynik
mają osobne typy błędów. Komunikaty nie zawierają surowych odpowiedzi ani sekretów.
Redirecty HTTP są wyłączone. Adapter nie odpytuje statusu w nieskończonej pętli.

Ten etap nie zmienia jeszcze GenerationJob w DB ani stanu Video i nie generuje
Asset automatycznie. Trwałe zapisywanie referencji, odzyskiwanie po restarcie,
rozstrzyganie niejednoznacznego submitu, kolejki i limity ponowień należą do integracji
workflow z workerami (etap 14). Nie należy utożsamiać enumu statusu providera z
obecnym czterostanowym enumem GenerationJob bez jawnego mapowania.

Testy `tests/unit/test_runpod.py` obejmują mock, kontrakt HTTP adaptera live,
konfigurację, błędy, ograniczenia i retries. Transport HTTP jest zastąpiony
`httpx.MockTransport`; testy nie uruchamiają GPU ani nie wymagają konta Runpod.

## Etap 13 — ElevenLabs i audio scen

`TTSProvider.synthesize(TTSRequest)` przyjmuje tekst (maks. 4000 znaków), voice_id,
język `pl/en` i ustawienia głosu. Zwraca `TTSResult`: bajty audio, MIME, opcjonalne
alignment/normalized_alignment, ID wywołania i raportowaną liczbę rozliczonych znaków.
Modele są walidowane przez Pydantic; tablice timestampów muszą mieć zgodne długości,
nieujemne czasy i uporządkowane początki. Audio nie trafia do serializacji JSON wyniku.

`MockTTSProvider` działa bez sieci, generuje poprawny **cichy WAV PCM mono 16 kHz**
i przybliżone timestampy. To materiał testowy do pipeline’u, nie lektor ani model
mowy. Wynik mocka ma koszt zero. `ElevenLabsProvider` używa httpx i endpointu
with-timestamps, dekoduje base64 oraz zwraca MP3 44.1 kHz / 128 kbps.
[API ElevenLabs](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps).

### Konfiguracja

Wybór providera przez `EXTERNAL_PROVIDERS_MODE=mock/live`. FastAPI udostępnia go
jako `app.state.tts` i zamyka klienta HTTP przy shutdown. Sam start nie generuje mowy.

| Zmienna .env | Znaczenie / domyślnie |
| --- | --- |
| `ELEVENLABS_API_KEY` | SecretStr, wymagany do wywołania live |
| `ELEVENLABS_MODEL` | model obsługujący TTS, wymagany w live |
| `ELEVENLABS_VOICE_ID` | głos domyślny; można przekazać głos do usługi |
| `ELEVENLABS_TIMEOUT_SECONDS` | 60 s |
| `ELEVENLABS_MAX_RESPONSE_BYTES` | 16777216, limit JSON z base64 |
| `ELEVENLABS_SEND_LANGUAGE_CODE` | true; ustaw false, jeśli model nie przyjmuje language_code |
| `TTS_USD_PER_1000_CHARACTERS` | własna stawka do estymacji; brak wartości blokuje usługę audio live |

Nie ma domyślnego cennika ElevenLabs. Stawkę ustala operator według swojej umowy
i modelu. Wartość jest szacunkiem na 1000 znaków wejścia, nie konwersją kredytów
ani potwierdzoną kwotą rachunku. Parametry są przekazywane przez Compose.
Globalny tryb live nadal wymaga konfiguracji istniejącego adaptera OpenAI.

VoiceSettings udostępnia stability, similarity_boost i speed. Zgodność głosu,
modelu i ustawień trzeba sprawdzić dla własnego konta. Adapter ogranicza rozmiar
odpowiedzi, sprawdza nagłówek MP3, nie podąża za redirectami i nie loguje treści
odpowiedzi. Pełne dekodowanie i jakość dźwięku należą do późniejszego quality control.

### Zapis assetu i kosztu

Wewnętrzna usługa `app.modules.audio.service.generate_scene_audio()`:

1. Sprawdza właściciela filmu i przynależność sceny; pobiera narrację z bazy.
2. Pod blokadą Video tworzy GenerationJob i CostEvent, zatwierdzając je przed TTS.
3. Wywołuje provider i zapisuje raportowane użycie, zanim rozpocznie zapis pliku.
4. Zapisuje plik przez StorageProvider, następnie Asset typu audio z timestampami
   i status succeeded w jednej transakcji DB.

Fingerprint tekstu, głosu, języka, ustawień, modelu i providera jest kluczem
idempotencji w scenie. Gotowy wynik jest zwracany ponownie bez drugiego wywołania.
Istniejący running/failed daje AudioConflict; usługa nie uruchamia automatycznie
kolejnego płatnego wywołania. Nowe parametry oznaczają osobne zadanie.
Usługa samodzielnie zatwierdza transakcje — przekazuj jej osobną sesję bez innych
niezatwierdzonych zmian. Nie podłączono jej jeszcze do publicznego endpointu HTTP.

Przykład wewnętrznego użycia dla istniejącej sceny (UUID właściciela, filmu i sceny):

```python
from sqlmodel import Session
from app.core.config import get_settings
from app.db.session import get_engine
from app.integrations.elevenlabs.factory import create_tts_provider
from app.integrations.storage.factory import create_storage_provider
from app.modules.audio.service import generate_scene_audio

settings = get_settings()
tts = create_tts_provider(settings)
storage = create_storage_provider(settings)
try:
    with Session(get_engine(), expire_on_commit=False) as db:
        asset = generate_scene_audio(db, owner_id, video_id, scene_id, tts, storage, settings)
finally:
    tts.close()
    storage.close()
```

Konfiguracja storage musi odpowiadać przekazanemu adapterowi. Lokalnie powstaje
plik w data/assets, w Compose w prywatnym bucketcie SeaweedFS. Nie dodano
publicznego dostępu do plików ani endpointu omijającego JWT. Video zachowuje swój
status; etap workerów będzie decydował o kolejności generowania i zmianach statusu.

Migracja `0011` dodaje SQLModel `CostEvent`: provider, operation, model, video_id,
channel_id, generation_job_id, estimated_cost_usd, nullable actual_cost_usd,
metadata_json i created_at. Estymator jest oddzielony od usługi audio.
Raportowany nagłówek `character-cost` i `request-id` zapisujemy w metadanych.
`actual_cost_usd` w live pozostaje null, ponieważ liczba rozliczonych znaków nie
jest kwotą w USD. Jeśli nagłówek jest niedostępny, billed_characters jest null.
[Metadane użycia ElevenLabs](https://elevenlabs.io/docs/api-reference/introduction).

Przy timeout/5xx lub niepoprawnej odpowiedzi wynik może być nieznany, a operacja
mogła zostać rozliczona. POST nie jest automatycznie ponawiany, również po 429.
Koszt szacowany pozostaje zapisany; nie zakładamy, że błąd oznacza darmowe wywołanie.
Po błędzie storage zachowujemy zgłoszone użycie. Przy błędzie zapisu Asset próbujemy
usunąć plik i oznaczamy job jako failed bez surowego komunikatu dostawcy.

Nie ma transakcji obejmującej jednocześnie provider, storage i DB. Awaria procesu
może zostawić running lub osierocony plik; odzyskiwanie musi być jawne, bez ślepego
ponawiania TTS. Usuwanie kanału usuwa metadane i koszty przez kaskadę; pliki nadal
wymagają sprzątania. Pełne budget enforcement i raportowanie kosztów to etap 19.

Uruchomienie i migracja:

```sh
docker compose up -d --build
# Alternatywnie poza Dockerem:
uv run --no-active alembic upgrade head
```

Testy TTS i zapisu audio są w `tests/unit/test_tts.py` oraz
`tests/integration/test_audio_storage.py`. Testy adaptera live korzystają z
MockTransport, bez połączeń do ElevenLabs i bez kosztów GPU/TTS.

## Etap 14 — kolejki i workery

Wybrano **Dramatiq + Redis**: wykorzystuje istniejący Redis, ma prosty model actorów
i wystarcza do modularnego monolitu. PostgreSQL pozostaje źródłem stanu zadania;
Redis przenosi wyłącznie jego UUID. Kolejki: `content`, `research`, `image`, `video`,
`audio`, `render`, `quality`. Kolejka `render` działa od etapu 15, a `quality` od etapu 16.
[Zasady dostarczania Dramatiq](https://dramatiq.io/best_practices.html).

### Uruchomienie

```sh
docker compose up -d --build --wait
docker compose logs -f worker dispatcher
```

Poza Dockerem zastosuj migracje i uruchom API, a w osobnych terminalach:

```sh
uv run --no-active dramatiq app.workers.actors --processes 1 --threads 4
uv run --no-active python -m app.workers.dispatcher
```

Wszystkie procesy muszą mieć tę samą bazę, Redis i konfigurację providerów/storage.
Dla local storage muszą też widzieć ten sam katalog. Compose używa wspólnego S3.
Migracje wykonuje API, worker i dispatcher nie uruchamiają ich równolegle.
Dispatcher jest technicznym procesem dostarczania istniejących zadań, nie schedulerem
publikacji z etapu 17. Zatrzymanie workera nie blokuje przyjmowania nowych zadań.

`TASK_DISPATCH_INTERVAL_SECONDS=2` określa interwał dispatchera.
`TASK_LEASE_SECONDS=300` określa termin sprawdzenia niedokończonej pracy.
`TASKS_EAGER=false` jest domyślne i wymuszone w Compose. Tryb eager jest wyłącznie
narzędziem testów regresyjnych wcześniejszych usług; wykonuje je synchronicznie.
Nowe testy asynchronicznego API jawnie go wyłączają, a integracja testuje prawdziwy
Redis i worker Dramatiq. Nie włączaj eager w uruchomionej aplikacji.

### Kontrakt API

Poniższe POST wymagają JWT właściciela i zwracają `202` + TaskRead oraz Location:

- `/channels/{id}/analyze`
- `/channels/{id}/ideas/generate`
- `/channels/{id}/competitor-research`
- `/videos/{id}/research`
- `/videos/{id}/script/generate`
- `/videos/{id}/top5-script/generate`
- `/videos/{id}/direct`
- `/videos/{id}/audio` z body `{"scene_id":"UUID"}`
- `/videos/{id}/visuals/image` lub `/visuals/video` z tym samym body

Wszystkie ścieżki mają prefiks `/api/v1`. Przykład:

```sh
curl --fail-with-body -X POST \
  "http://localhost:8000/api/v1/channels/$CHANNEL_ID/analyze" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: channel-analysis-v1"

curl --fail-with-body "http://localhost:8000/api/v1/tasks/$TASK_ID" \
  -H "Authorization: Bearer $TOKEN"
```

TaskRead zawiera id, kind, status, video_id, attempts, error, result i czasy.
Statusy: queued, running, succeeded, failed, needs_review. `result` po sukcesie
zawiera dotychczasowy wynik operacji. Błędy wykonania są widoczne w zadaniu,
a nie w odpowiedzi POST, która potwierdza jedynie przyjęcie. Endpoint GET zadania
oraz `GET /videos/{id}/tasks` (do 100 najnowszych) weryfikują właściciela; cudze
zasoby dają 404. Nieaktywny użytkownik nie może uruchomić czekającego zadania.

Opcjonalny `Idempotency-Key` jest unikalny w obrębie użytkownika. Taki sam klucz
i parametry zwracają poprzednie zadanie, zmienione parametry dają 409. Bez nagłówka
POST tworzy nowe zlecenie, więc przy ponawianiu po błędzie sieci zachowaj klucz.
To deduplikacja zleceń backendu, nie gwarancja exactly-once płatnego API.

`POST /ideas/{id}/create-video` zwraca `202` z VideoRead. Zapis filmu, zużycie pomysłu
i pierwsze zadanie są atomowe w PostgreSQL. Status filmu początkowo wynosi
IDEA_GENERATED; SCRIPTING/RESEARCHING ustawia worker po podjęciu pracy.
Replay zwraca istniejący film (`200`) bez drugiego workflow.

Automatyczny łańcuch na tym etapie:

- STORY: scenariusz → Director → SCRIPT_READY.
- TOP5: research → scenariusz z faktów → Director → SCRIPT_READY.

TOP5 nadal wymaga prawdziwego korpusu źródeł; pusty LocalResearchProvider zatrzymuje
przetwarzanie jako failed, zamiast wymyślać fakty. Nie dodano wyszukiwarki.
Audio zlecane osobno zapisuje prawdziwy Asset i CostEvent. Od etapu 15 zadania
wizualne zapisują również Asset, a mock generuje techniczne PNG/MP4. Po ukończeniu
wszystkich scen można osobno zlecić render opisany poniżej.

### Dostarczanie, ponowienia i restart

Migracja `0012` dodaje SQLModel Task z parametrami, wynikiem, checkpointem providera,
czasem kolejnej próby, licznikiem, tokenem wykonania i statusem. Wiersz Task jest
jednocześnie trwałym outboxem. API nie wysyła do Redis w swojej transakcji.
Dispatcher cyklicznie wysyła oczekujące UUID; po awarii Redis lub utracie wiadomości
spróbuje ponownie. Kolejne dostarczenie po 30 sekundach może duplikować wiadomość.

Worker korzysta z blokady advisory PostgreSQL trzymanej przez całe wykonanie,
także pomiędzy commitami usług. Duplikat nie uruchamia drugiej operacji jednocześnie.
Zakończone zadanie jest pomijane. Commit wyniku i utworzenie następnego zadania są
atomowe. Token wykonania chroni zakończenie przez poprzednie wykonanie.

Po restarcie można odzyskać już zapisany scenariusz/research, ponowić lokalnego
Directora i odczytać gotowy wynik audio. Znane ID Runpod pozwala kontynuować status
bez ponownego submitu. Status jest odpytywany co co najmniej 5 sekund, z ograniczonym
terminem całego odpytywania. Chwilowe błędy odczytu mają ograniczony backoff i limit
prób. Dramatiq nie stosuje dodatkowej warstwy automatycznych retries aktora.

Jeśli proces zginął podczas niejednoznacznego LLM/TTS/submitu Runpod, zadanie trafia
do needs_review zamiast powtarzać płatną operację. Nie ma jeszcze publicznego
endpointu zatwierdzania takich ponowień; należy najpierw ustalić wynik u dostawcy.
Nie obiecujemy atomowej transakcji PostgreSQL–Redis–provider ani exactly-once GPU.

## Etap 15 — Render Engine

`RenderService` pobiera scenariusz i assety filmu ze SQLModel oraz prywatnego storage.
Silnik `FFmpegRenderer` operuje wyłącznie na lokalnych plikach, bez zależności od
OpenAI/Runpoda. Składa obrazy PNG/JPEG i klipy MP4 w pionowy `final.mp4`:
1080×1920, H.264/yuv420p, AAC 48 kHz stereo, 30 fps, `faststart`.
Obrazy obsługują `zoom_in`, `zoom_out`, `pan_left`, `pan_right`, `static`.
Klipy są przycinane do pionowego kadru i zapętlane do długości sceny; ich własna
ścieżka audio jest pomijana. Narracja jest uzupełniana ciszą do końca sceny;
zbyt długa narracja powoduje błąd zamiast jej ucięcia.

Napisy ASS są wypalane w filmie i przechowywane jako osobny Asset. Korzystają
z alignmentu TTS; bez niego dzielą tekst na fragmenty o równych przedziałach czasu.
Opcjonalna muzyka jest zapętlana, ściszana do 12% i dodatkowo automatycznie
wyciszana podczas narracji (`sidechaincompress`). Opis filtrów:
[dokumentacja FFmpeg](https://ffmpeg.org/ffmpeg-filters.html).

### Przepływ API (wszystkie wywołania wymagają JWT właściciela)

1. Poczekaj na zakończenie Directora i odczytaj `GET /api/v1/videos/{video_id}/direction`
   — `scenes[].id` to identyfikatory scen do kolejnych wywołań.
2. Dla każdej sceny zleć `POST /api/v1/videos/{video_id}/visuals/image` lub `/visuals/video`
   zgodnie z `visual_type`, oraz `POST /api/v1/videos/{video_id}/audio`.
   Body obu operacji: `{"scene_id":"UUID"}`. Poczekaj na `succeeded` wszystkich zadań.
3. `POST /api/v1/videos/{video_id}/render` z body `{}` albo
   `{"music_asset_id":"UUID"}` zwraca `202` i Task. Muzyka musi być istniejącym
   assetem audio tego samego filmu; upload/biblioteka muzyczna nie są częścią tego etapu.
4. Odpytuj `GET /api/v1/tasks/{task_id}`. Wynik zawiera `asset_id`,
   `subtitle_asset_id`, `quality_task_id` oraz `status: "QUALITY_CHECK"`.
   Od etapu 16 worker automatycznie zleca QC; śledź `quality_task_id` i raporty
   `GET /api/v1/videos/{video_id}/quality-checks`.
5. `GET /api/v1/assets/{asset_id}/download` pobiera plik przez uwierzytelnione API.
   `GET /api/v1/videos/{video_id}/assets` zwraca listę plików bez kluczy storage i sekretów.

Render zawsze działa asynchronicznie. Obsługuje nagłówek `Idempotency-Key`.
Pierwsze wykonanie zapisuje w Task niezmienny manifest: kolejność, tekst, ruch,
czas scen oraz najnowsze pasujące assety. Blokada filmu zapobiega równoległym
renderom różnych zadań. Brak assetów nie przesuwa filmu do następnego stanu.
Gotowy plik, napisy i przejście `RENDERING → QUALITY_CHECK` są zatwierdzane razem.
Sam renderer nie ustawia `READY`; robi to kontrola jakości z etapu 16.

Po restarcie worker może odtworzyć lokalny render z manifestu lub odzyskać
już zapisany wynik. Błędy FFmpeg/storage mają ograniczone ponowienia; wyczerpanie
prób pozostawia zadanie `needs_review` i film `RENDERING` do interwencji operatora.
Nie ma jeszcze panelu ani endpointu ręcznego wznowienia takiego zadania.
Niepewny commit nie usuwa plików: stabilne klucze zostaną nadpisane przy wznowieniu.
Usuwanie osieroconych obiektów po trwałej awarii pozostaje zadaniem utrzymaniowym.

### Wyniki wizualne Runpoda

Mock zapisuje techniczne obrazy i klipy testowe, nie wizualizacje AI. W trybie live
worker Runpoda otrzymuje `parameters.output_key` i `parameters.output_bucket`.
Musi zapisać PNG/MP4 w tym bucketcie przy użyciu własnej konfiguracji S3 i zwrócić:

```json
{"object_key":"generated/TASK_UUID/visual.png","content_type":"image/png"}
```

Dla video: `visual.mp4` i `video/mp4`. Backend akceptuje wyłącznie dokładnie
przydzielony klucz. Nie pobiera dowolnych URL ani obiektów przypisanych innym zadaniom.
Produkcja wymaga dostosowania workera Runpoda do tego kontraktu; nie testowano
płatnego endpointu live. JSON ukończonego providera jest zapisywany przed importem
pliku, więc wznowienie nie musi ponawiać generowania.

### Uruchomienie i limity

Obraz Docker i GitHub Actions instalują FFmpeg, ffprobe i font DejaVu Sans.
Lokalnie zainstaluj FFmpeg z libx264/libass oraz font DejaVu Sans. Migracja `0013`
dodaje rodzaj zadania `render`. Po aktualizacji: `docker compose up --build -d`.

Konfiguracja `.env`: `FFMPEG_BINARY`, `FFPROBE_BINARY`, `RENDER_TIMEOUT_SECONDS=600`,
`RENDER_THREADS=2`, `RENDER_MAX_INPUT_BYTES=536870912` (suma wejść).
`STORAGE_MAX_BYTES` nadal ogranicza każdy plik, także końcowy MP4.
Maksimum: 100 scen i 180 sekund filmu. Worker potrzebuje miejsca tymczasowego
na wejścia, zakodowane klipy i finalny film. Timeout obejmuje pracę FFmpeg całego
renderu; transfery storage mają osobne timeouty adaptera.

Pobrane pliki są weryfikowane przez rozmiar i SHA-256. FFmpeg dostaje konkretne
demuxery i whitelistę protokołów `file,pipe`; napisy nie mogą wstrzykiwać poleceń ASS.
Od etapu 16 działa techniczna kontrola jakości. Brak jeszcze automatycznej publikacji
i automatycznego wyzwalania pierwszego renderu po ukończeniu wszystkich assetów.

## Etap 16 — Quality Control

Po zakończeniu renderu worker atomowo zapisuje wynik zadania i zleca zadanie
`quality`. `QualityCheck` (SQLModel, migracja `0014`) przechowuje historię ocen,
przypisany render/asset, wyniki poszczególnych kontroli, progi, numer próby oraz
identyfikatory zadań naprawczych. Jednemu renderowi odpowiada jeden raport.
Ponowne dostarczenie zadania lub restart workera nie tworzą dodatkowych napraw.

### Kontrole techniczne

- Finalny MP4 istnieje w skonfigurowanym prywatnym storage.
- Rozmiar i SHA-256 plików odpowiadają metadanym; wymagane assety nie są puste.
- Film ma 1080×1920, H.264, AAC i 30 fps; ścieżka audio ma dodatni czas trwania.
- Czas filmu różni się od `Video.duration_target` najwyżej o skonfigurowaną
  tolerancję (domyślnie 1 s), a od sumy scen w manifeście najwyżej o 0,25 s.
- Manifest zawiera wszystkie sceny, w poprawnej kolejności i bez duplikatów.
  Każda ma wizualny asset i narrację przypisane do tego samego filmu i sceny.
- FFmpeg dekoduje plik końcowy i wymagane fragmenty assetów. Same nagłówki
  odczytane przez ffprobe nie wystarczają do uznania pliku za poprawny.

Używamy `-xerror` i `-err_detect explode`; szczegóły opisuje
[dokumentacja FFmpeg](https://ffmpeg.org/ffmpeg.html). FFmpeg nadal dostaje prywatne
pliki lokalne, określone demuxery, ograniczenia czasu i protokoły `file,pipe`.

Przy pozytywnej kontroli raport otrzymuje `passed`, a film `READY`. To gotowość
techniczna; nie jest oceną atrakcyjności, prawdziwości treści ani zgodności obrazu
z narracją. Obecność ścieżki audio nie oznacza wykrycia mowy — mock TTS jest cichy.

### Naprawa scen i ograniczenia

Gdy wszystkie wykryte usterki można przypisać do assetów scen, QC zleca tylko
wymagane zadania `image`, `video` lub `audio`. Zdrowe assety i scenariusz pozostają
z pierwotnego manifestu. Naprawa TTS ma osobny klucz idempotencji przypisany do
raportu QC, więc może utworzyć nowy plik, ale retry tej samej naprawy nie nalicza
kolejnego generowania.

Raport jest wtedy `repairing`, a film pozostaje w `QUALITY_CHECK`. Po zakończeniu
napraw raport otrzymuje `repaired`, film wraca do `GENERATING_ASSETS`, a worker
zleca złożenie nowego MP4 i kolejną kontrolę. Ponowne złożenie całego kontenera MP4
jest konieczne; ponowne generowanie AI dotyczy tylko wadliwych assetów.
Domyślnie dopuszczamy maksymalnie **2 rundy napraw na film**. Nie zmieniamy
automatycznie scenariusza ani nie skracamy zbyt długiej narracji.

Błąd całego filmu (np. brak finalnego MP4, zły czas lub rozdzielczość), brak
poprawnego manifestu, błąd zadania naprawczego, timeout oczekiwania albo
wyczerpanie limitu kończą kontrolę jako `failed` i film jako `FAILED`.
Endpoint retry z etapu 18 obsługuje bezpieczne ponowienia wybranych zadań;
nie resetuje nieudanego QC ani limitów jego napraw. Przejście `QUALITY_CHECK → GENERATING_ASSETS` wykonuje wyłącznie
wewnętrzna ścieżka napraw; nie ma endpointu do dowolnej zmiany stanu filmu.

Awaria storage/narzędzia lub niedostępność providera vision nie jest oceną
wadliwej sceny. Takie błędy mają ograniczone retry zadania (domyślnie 3 próby),
a po ich wyczerpaniu Task ma `needs_review` i film pozostaje `QUALITY_CHECK`.
Nie uruchamiamy wtedy ponownego płatnego generowania. W raporcie może pozostać
`checking`; stan błędu wykonania jest dostępny przez jego `task_id`.

### API

Wszystkie endpointy wymagają JWT właściciela filmu:

```http
GET /api/v1/videos/{video_id}/quality-checks
POST /api/v1/videos/{video_id}/quality-check
GET /api/v1/tasks/{task_id}
```

POST nie wymaga body, zwraca `202` i ten sam Task QC dla ostatniego ukończonego
renderu. Pozwala też zlecić kontrolę filmu wyrenderowanego przed wdrożeniem etapu 16.
GET zwraca kolejne raporty: `checking`, `repairing`, `repaired`, `passed`, `failed`,
w tym listę `report_json.checks` z kodem, wynikiem (`passed`/`failed`/`skipped`),
identyfikatorem sceny i assetu. Raporty nie zawierają ścieżek lokalnych ani sekretów.
Task `succeeded` oznacza wykonanie kontroli — jej werdykt może być `failed`.
Po naprawie `rerender_task_id` wskazuje następny render; jego wynik wskazuje nowe QC.

### Interfejs oceny wizualnej

`VisualQualityProvider.check(VisualQualityRequest) -> VisualQualityResult` jest
punktem rozszerzenia w `app/modules/quality/provider.py`. Adapter zgłasza
`requires_frames`, otrzymuje identyfikator sceny, jej prompt i lokalne klatki PNG
z finalnego filmu. Fabrykę providera można wymienić bez zmian w rendererze.

Domyślny adapter `disabled` zwraca **skipped**, nie pozoruje oceny vision i nie
wykonuje płatnych calli. Przyszły adapter powinien walidować odpowiedź do modelu
Pydantic, zwracać bezpieczne kody i zgłaszać `VisualQualityUnavailable` przy
awarii. Negatywna ocena konkretnej sceny korzysta z tej samej ograniczonej ścieżki
napraw. Testowy adapter sprawdza rzeczywiste klatki przekazane przez tę ścieżkę.

### Konfiguracja i uruchomienie

```dotenv
QUALITY_DURATION_TOLERANCE_SECONDS=1.0
QUALITY_MAX_SCENE_RETRIES=2
QUALITY_TIMEOUT_SECONDS=180
QUALITY_REPAIR_TIMEOUT_SECONDS=3600
```

`QUALITY_MAX_SCENE_RETRIES=0` wyłącza automatyczne naprawy. Timeout QC ogranicza
operacje FFmpeg; transfery mają timeout adaptera storage. Suma pobieranych plików
(wejścia i finalny film) podlega `RENDER_MAX_INPUT_BYTES`, a pojedynczy plik
`STORAGE_MAX_BYTES`. Progi i limit napraw są zapisane w raporcie.

Po aktualizacji uruchom `docker compose up -d --build`; API wykona migrację.
Dla instalacji lokalnej: `uv run alembic upgrade head`, a następnie restart
API, workera i dispatchera. Scheduler jest opisany w sekcji etapu 17 poniżej.

## Etap 17 — Scheduler

Osobny proces `python -m app.workers.scheduler` okresowo sprawdza aktywne kanały
aktywnych użytkowników. Dla bieżącego dnia oblicza różnicę między
`Channel.videos_per_day` a liczbą utworzonych rekordów Video. Liczy wszystkie filmy
z tego dnia, także utworzone ręcznie, przetwarzane i zakończone błędem. Dzięki temu
nie zastępuje bez końca nieudanych filmów kolejnymi płatnymi próbami.

### Zasady planowania

- `draft`, `paused` i kanały wyłączonych użytkowników są pomijane.
- Kanał musi mieć gotowy blueprint: odbiorców i przynajmniej jeden content pillar.
  Scheduler nie zleca automatycznie analizy; brak konfiguracji oznacza
  `blocked / blueprint_not_ready`.
- `manual`: planner uzupełnia pulę pomysłów, ale tworzy filmy tylko z pomysłów
  zatwierdzonych przez użytkownika. Aktywny kanał nie wymaga osobnego kliknięcia
  create-video po approve. Powtórne wywołanie create-video zwróci istniejący film.
- `semi_auto`: planner najpierw wykorzystuje pomysły zatwierdzone, potem wybiera
  kandydatów. Preferuje format niedoreprezentowany w planie według blueprintu,
  a w jego obrębie najstarszy temat. Jest to deterministyczna heurystyka, nie ocena
  popularności przez AI. Pomysły w innym języku lub z wyłączonego formatu są pomijane.
- Istniejące pomysły są używane przed generowaniem nowych. Oczekujące na akceptację
  tematy ograniczają dalsze generowanie w manual; backlog nie rośnie przy każdym ticku.
- Nowe pomysły powstają przez istniejący Task `ideas`, w partiach 10–20 zgodnych
  z kontraktem Idea Engine. Domyślnie najwyżej 2 takie zadania na kanał i dzień.
  Błąd lub niepewny wynik zaplanowanego generatora blokuje nowe automatyczne partie
  tego dnia. Ręczne dostarczenie/zaakceptowanie dostępnych pomysłów nadal pozwala
  wypełnić plan.
- W trakcie oczekującej analizy lub generowania pomysłów dla kanału planner czeka,
  aby nie zmieniać wersji kanału używanej przez trwające wywołanie AI.

Nowe Video oraz pierwsze zadanie workflow powstają atomowo. STORY uruchamia
scenariusz → Director; TOP5 uruchamia research → scenariusz → Director.
**Obecny automatyczny łańcuch kończy się na `SCRIPT_READY`.** Generowanie assetów,
audio i pierwszy render nadal zleca się endpointami opisanymi w etapach 15–16;
render automatycznie prowadzi już przez QC do READY. Nie dodano publikacji.
TOP5 nadal wymaga źródeł — pusty provider researchu zatrzyma jego przetwarzanie.

### Trwałość, dzień i współbieżność

Migracja `0015` dodaje SQLModel `DailyPlan` i indeks zadań kanału. Plan jest
unikalny dla `(channel_id, day)`; zachowuje strefę, granice dnia w UTC, bieżący cel,
stan, bezpieczny kod blokady i ID zaplanowanych zadań generowania pomysłów.
Stany: `complete`, `waiting_approval`, `waiting_task`, `blocked`.
`complete` oznacza osiągniętą liczbę utworzonych filmów, nie zakończenie ich renderów.

Krótka transakcja z blokadami User → Channel obejmuje plan, zmiany pomysłów,
Video i task outbox. Można uruchomić kilka schedulerów; ponowny tick i restart
nie dublują filmów ani partii pomysłów. Scheduler nie łączy się z providerami ani
brokerem — wysyłką zapisanych Task zajmuje się dispatcher. Kanały są skanowane
stronami po ID, więc pierwsza partia nie blokuje obsługi kolejnych kanałów.

Dzień domyślnie oznacza **UTC**, a konfiguracja IANA, np. `Europe/Warsaw`, uwzględnia
dni 23/25-godzinne przy zmianie czasu. Okno jest domknięte z lewej i otwarte z prawej.
Utworzony plan zachowuje swoje granice. Strefa jest wspólna dla instalacji; zmianę
strefy najlepiej wykonać po zakończeniu bieżącego dnia. Scheduler nie nadrabia
poprzednich dni. Zwiększenie videos_per_day uzupełnia bieżący plan; zmniejszenie
nie usuwa już utworzonych filmów. Ręczne create-video może przekroczyć cel — jest
to limit automatycznego planowania, nie twardy limit uprawnień użytkownika.

Worker przed automatycznym generowaniem pomysłów ponownie sprawdza aktywność
kanału, włączenie schedulera, ważność okna i dzienny limit. Nieaktualne zadanie
kończy się `succeeded` z `{"skipped":true,"reason":"..."}`, bez calla providera.
Wstrzymanie kanału zatrzymuje nowe planowanie i nieuruchomione automatyczne partie;
nie cofa już utworzonych filmów ani rozpoczętych wywołań. Także pominięte zadanie
zużywa miejsce w dziennym limicie partii.

### Uruchomienie

```dotenv
SCHEDULER_ENABLED=true
SCHEDULER_INTERVAL_SECONDS=60
SCHEDULER_TIMEZONE=UTC
SCHEDULER_BATCH_SIZE=100
SCHEDULER_MAX_IDEA_BATCHES_PER_DAY=2
```

```sh
docker compose up -d --build
docker compose logs -f scheduler
```

Proces czeka na zdrowe API po migracjach, obsługuje SIGTERM/SIGINT i zwalnia pool
połączeń przy wyjściu. Interwał jest przerwą między przebiegami, nie godziną publikacji.
Obraz Docker zawiera `tzdata`. Lokalnie wymagane są dane stref czasowych systemu.

Jednorazowy przebieg (wykonuje rzeczywiste planowanie, nie dry-run):

```sh
uv run --no-active python -m app.workers.scheduler --once
# albo w działającym Compose:
docker compose exec scheduler python -m app.workers.scheduler --once
```

`--once` wypisuje JSON z liczbą przeskanowanych kanałów, utworzonych filmów,
zleconych partii i błędów. Błąd jednego kanału nie blokuje pozostałych; proces
jednorazowy zwraca kod 1, jeżeli wystąpiły błędy techniczne. Wyłączony scheduler
zwraca zera. Nie dodano endpointów panelu ani schedulera publikacji.

## Etap 18 — API panelu

Wszystkie poniższe ścieżki mają prefix `/api/v1` i wymagają JWT
(`Authorization: Bearer ...`). Dane są ograniczone do właściciela; cudze
identyfikatory zwracają `404`, również dla administratora. JSON zawiera UUID
jako stringi, daty ISO i kwoty Decimal jako stringi, bez utraty precyzji.
Backend nie wymaga konkretnego frameworka frontendu.

| Metoda | Ścieżka | Wynik |
| --- | --- | --- |
| GET | `/dashboard` | Liczby kanałów, aktywnych kanałów, filmów według statusu, koszty i 10 ostatnich filmów |
| GET | `/channels/{id}/overview` | Kanał z blueprintem, liczniki pomysłów/filmów, koszty i ostatni dzienny plan schedulera |
| GET | `/channels/{id}/ideas` | Istniejąca stronicowana lista pomysłów |
| GET | `/channels/{id}/videos` | Stronicowana lista filmów; opcjonalny filtr `status`, np. `READY` |
| GET | `/channels/{id}/costs` | Stronicowane zdarzenia kosztowe i podsumowanie całego kanału |
| GET | `/videos/{id}` | Szczegóły filmu, status i snapshot blueprintu |
| GET | `/videos/{id}/scenes` | Sceny w kolejności, z trwałymi ID do edycji/regeneracji; pusta lista przed scenariuszem |
| GET | `/videos/{id}/status` | Stan filmu, `updated_at`, 100 ostatnich zadań oraz flaga aktywnych zadań |
| POST | `/videos/{id}/retry` | `202 TaskRead`; body `{"task_id":"UUID"}` |
| PATCH | `/scenes/{id}` | Zmieniona scena |
| POST | `/scenes/{id}/regenerate` | `202 TaskRead`; body `{"kind":"image"}`, `video` lub `audio` |

Listy filmów i kosztów: `limit=20` (1–100), `offset=0` (>=0), odpowiedź
`items/total/limit/offset`. Sortowanie po czasie i ID jest stabilne.
`status` zwraca zadania od najnowszych; `has_active_tasks` uwzględnia także
starsze zadania spoza limitu 100. Dashboard i koszty obejmują całą historię.
`latest_plan` może być z poprzedniego dnia — zawsze sprawdzaj jego `day`.

### Koszty

`estimated_usd` to suma oszacowań, `actual_usd` to suma znanych kosztów
rzeczywistych, a `effective_usd` używa dla każdego zdarzenia kosztu rzeczywistego
lub oszacowania, jeśli brak rozliczenia. `pending_actual_events` pokazuje liczbę
nierozliczonych zdarzeń. Rzeczywisty koszt równy zero jest poprawnym rozliczeniem.
Nie dodawaj sum estimated i actual do siebie. Endpoint pokazuje zapisane
CostEvent; zakres rejestrowania i egzekwowania budżetu opisuje etap 19 poniżej.
Brak zdarzenia nie oznacza, że zewnętrzne wywołanie było bezpłatne.

### Edycja i regeneracja

PATCH przyjmuje dowolny niepusty podzbiór: `narration`, `visual_prompt`, `mood`,
`caption_emphasis`. Jawne `null`, nieznane pola i puste body zwracają `422`.
Edycja jest możliwa w `SCRIPT_READY`, bez aktywnych zadań filmu i zanim dana scena
ma historię generacji lub assety. Zmiana narracji TOP5 jest blokowana, żeby
zachować powiązanie ze zweryfikowanymi faktami. Czas, typ wizualny i kolejność
scen nie są edytowalne w tym kontrakcie — wpływają na walidację scenariusza
oraz plan i wycenę Directora. Konflikty stanu zwracają `409`.

Regeneracja działa w `SCRIPT_READY`, bez aktywnych zadań filmu. Dla wizualiów
`kind` musi odpowiadać `visual_type` sceny. `audio` tworzy nową wersję TTS;
pozostałe sceny i stare assety pozostają dostępne. Zadanie `needs_review` tego
samego rodzaju dla sceny blokuje nową generację do wyjaśnienia wyniku.
Po zmianie assetów wybierz ich nowe ID w żądaniu renderowania.

Wysyłaj `Idempotency-Key` przy regeneracji: ten sam klucz i payload zwracają
to samo zadanie (również po zakończeniu); inny payload z tym kluczem daje `409`.
Nowy klucz oznacza świadome zlecenie nowej generacji i potencjalny koszt.
Operacje generacji nie są wykonywane w HTTP, nawet przy `TASKS_EAGER=true`.
Odpowiedzi `202` mają `Location: /api/v1/tasks/{id}`.

### Retry

Retry wznawia wskazane zadanie `failed`, zachowując ID, checkpoint i historię
prób. Równoległe wywołania podczas oczekiwania/pracy zwracają to samo zadanie.
Limit to 3 ręczne wznowienia na zadanie i maksymalnie 10 wykonanych prób;
liczniki nie są resetowane. Wznowienie błędu STORY/research/TOP5 może przywrócić
odpowiedni poprzedni etap filmu, zapisując jawny wpis historii w tej samej
transakcji co zadanie. Typ i aktualny etap muszą do siebie pasować.

`needs_review`, zakończone zadania, wyczerpany limit, naprawy QC, audio oraz
wysłana już generacja wizualna zwracają `409`. Audio ponawiaj przez regenerację
sceny po wyjaśnieniu ewentualnej niepewności. Retry nie odblokowuje dowolnie
filmu READY/PUBLISHED, nie kasuje checkpointów providerów i nie resetuje QC.

Przykłady (zmienne ID i TOKEN ustaw lokalnie):

```sh
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/dashboard
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/channels/$CHANNEL_ID/videos?limit=20&status=SCRIPT_READY"
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"mood":"quiet suspense"}' "http://localhost:8000/api/v1/scenes/$SCENE_ID"
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: panel-audio-1' -d '{"kind":"audio"}' \
  "http://localhost:8000/api/v1/scenes/$SCENE_ID/regenerate"
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"task_id\":\"$TASK_ID\"}" "http://localhost:8000/api/v1/videos/$VIDEO_ID/retry"
```

Etap nie zmienia schematu bazy. Zaktualizuj procesy przez
`docker compose up -d --build`. Kontrola budżetu jest opisana w etapie 19 poniżej.

## Etap 19 — kontrola budżetu filmu

`BudgetService.can_spend()` porównuje planowany wydatek z pozostałą kwotą
`Video.budget_limit_usd`. Limit filmu jest snapshotem z momentu jego utworzenia;
późniejsza zmiana budżetu kanału nie zmienia istniejących filmów.

Przed płatną generacją worker blokuje rekord Video w PostgreSQL i zapisuje
CostEvent w tej samej transakcji co decyzję o przyznaniu środków. Commit następuje
przed wywołaniem providera. Równoległe zadania nie mogą wydać tej samej pozostałej
kwoty. Migracja `0016` dodaje unikalny `(video_id, operation_key)`; rezerwacja
wizualiów jest związana z ID zadania i nie powtarza się podczas polling/replay.
TTS zachowuje dotychczasową idempotency po GenerationJob.

Uwzględniony wydatek to dla każdego CostEvent `actual_cost_usd`, jeśli znany,
albo `estimated_cost_usd`. Niepewność, timeout, błąd storage i restart nie
zwalniają automatycznie środków. Zapobiega to ponawianiu płatnych prób poza
limitem. Odczyt budżetu:

```sh
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/budget"
```

Odpowiedź zawiera `video_id`, `currency`, `limit_usd`, `committed_usd` oraz
`remaining_usd` (Decimal jako stringi). Wymaga JWT i właściciela filmu.
Brak środków kończy zadanie jako `failed` z `error=budget_exceeded`, bez
uruchamiania providera. Synchroniczna ścieżka developerska zwraca `409`.
Ręczne retry i naprawy QC podlegają temu samemu budżetowi, a ich dotychczasowe
limity prób pozostają aktywne.

### Wycena i zakres

- Runpod: stawki operatora `DIRECTOR_IMAGE_ESTIMATE_USD` i
  `DIRECTOR_VIDEO_SECOND_ESTIMATE_USD`; koszt za czas zaokrąglany w górę do
  6 miejsc. Rezerwacja przed nowym submit, bez ponownej rezerwacji przy polling.
- TTS: `TTS_USD_PER_1000_CHARACTERS` i długość narracji; kontrola przed utworzeniem
  GenerationJob i wywołaniem ElevenLabs.
- LLM przypisany do Video (STORY, research, TOP5): wymagane
  `LLM_REQUEST_ESTIMATE_USD`. Każde wywołanie rezerwuje skonfigurowaną kwotę
  pomnożoną przez `1 + OPENAI_MAX_RETRIES`. Brak stawki w live blokuje generację.
  To konserwatywna wycena jednego żądania; dobierz ją do modelu, maksymalnego
  rozmiaru promptu/schematu i limitu tokenów odpowiedzi. Domyślnie `null`.
- Mock jest bezpłatny. TTS nadal zapisuje zdarzenie z actual=0; mock LLM i
  wizualia nie tworzą płatnych rezerwacji.

Stawki są konfigurowalne i nie stanowią aktualnego cennika providerów.
System egzekwuje limit według zapisanych kosztów i szacunków, nie gwarantuje
maksymalnej kwoty faktury przy zaniżonych stawkach. Automatyczne rozliczanie
faktur/actual kosztów oraz budżet całego konta pozostają rozszerzeniami.
Analiza kanału i generowanie pomysłów nie mają jeszcze przypisanego Video;
nie są objęte limitem pojedynczego filmu. Research provider jest obecnie lokalny.

### Tańsze wizualia

Director ogranicza nowy plan do minimum: dotychczasowego udziału budżetu wizualnego
oraz pozostałych środków filmu. Już zapisany plan pozostaje historyczną wyceną;
wykonanie zadań jest sprawdzane ponownie względem bieżącego budżetu.

Jeśli nowy job video nie mieści się w budżecie, ale obraz się mieści, scena
w `SCRIPT_READY` otrzymuje `visual_type=image` i `camera_motion=zoom_in`.
Wybór i rezerwacja są trwałe; worker wywołuje image provider, zapisuje PNG,
a renderer stosuje ruch. Rodzaj pierwotnego zadania pozostaje niezmieniony,
żeby zachować zgodność z Idempotency-Key żądania; efektywny rodzaj jest zapisany
w checkpoint i zdarzeniu kosztowym. Wynik zadania wskazuje rzeczywisty asset.

Nie zmieniamy rodzaju już wysłanego joba ani naprawy QC, która musi odpowiadać
utrwalonemu manifestowi. Jeśli również obraz przekracza dostępny budżet,
żadne nowe wywołanie nie jest wykonywane.

Aktualizacja: `docker compose up -d --build` uruchamia migrację `0016`.
W trybie live ustaw wcześniej stawki w `.env`. Obserwowalność opisuje etap 20 poniżej.

## Etap 20 — obserwowalność

Aplikacja zapisuje logi JSON z czasem UTC, poziomem, nazwą loggera i zdarzeniem.
Formatter zachowuje jawnie dozwolone pola: `request_id`, `task_id`, `video_id`,
`channel_id`, `scene_id`, `task_kind`, statusy, kategorię błędu i `duration_ms`.
Nie serializuje dowolnych obiektów `extra`, treści wyjątków, promptów, nagłówków
autoryzacji, parametrów SQL ani wyników generacji.

### Żądanie → zadanie → provider

Każda odpowiedź HTTP otrzymuje `X-Request-ID`, również odpowiedzi 401/404/422/500.
Możesz wysłać własny identyfikator (1–64 znaków ASCII: litery, cyfry, `_`, `-`);
niepoprawny lub brakujący nagłówek zostaje zastąpiony losowym UUID hex. Nagłówek
jest dostępny dla przeglądarki przez CORS, podobnie jak `Location`.
CORS dopuszcza także `Idempotency-Key`.

Przy zleceniu zadania jego `request_id` zostaje zapisany w PostgreSQL
(migracja `0017`). Worker odtwarza kontekst i przekazuje go kolejnym zadaniom
workflow, również po restarcie. Replay tego samego zadania zachowuje pierwotne
powiązanie, niezależnie od nowego ID żądania HTTP. Dla starych zadań i zadań
schedulera bez HTTP worker używa w logach `task-<UUID>`; pole oryginalnego
zadania w bazie może nadal być null. Kontekst jest izolowany między żądaniami
i wątkami oraz sprzątany po zakończeniu.

Zdarzenia:

- `http_request`: metoda, szablon trasy, status i czas obsługi HTTP; bez query
  string i surowej ścieżki dla nieznanych tras. Zastępuje access log Uvicorna.
- `provider_call`: rodzaj providera, nazwa adaptera (`provider_type`), operacja,
  wynik i czas jednego wywołania adaptera. Dotyczy LLM, Runpod i TTS używanych
  przez API/workery oraz operacji storage wykonywanych przez opakowany provider.
  Czas obejmuje wewnętrzne retry adaptera; polling jest osobnym wywołaniem,
  a nie pomiarem całego czasu pracy GPU.
- `worker_execution`: czas pojedynczego wywołania workera, bez oczekiwania
  w kolejce. `task_completed` i `task_stopped` opisują wynik kroku.

`error_category` odróżnia budżet, timeout, zależności, providera, nieprawidłowy
output, walidację, render, potrzebę weryfikacji i błąd wewnętrzny. `pending`
oznacza oczekiwanie na polling/naprawę, nie awarię. HTTP klasyfikuje też błędy
uprawnień, brak zasobu i konflikty stanu. Kategoria ostatniej próby jest zapisana
w Task, a po sukcesie lub ręcznym retry jest czyszczona. Stare zadania mogą mieć
kategorię null. Klasyfikacja bazuje na bezpiecznych typach wyjątków, nie analizie
ich treści; błąd timeout ukryty przez adapter jako ogólna niedostępność pozostanie
kategorią providera.

```sh
docker compose logs --no-log-prefix api worker dispatcher scheduler
curl -H "Authorization: Bearer $TOKEN" -H 'X-Request-ID: local-check-1' \
  http://localhost:8000/api/v1/dashboard
```

### Panel administratora

`GET /api/v1/admin/jobs` wymaga aktywnego użytkownika z rolą `admin` odczytaną
z bazy. Bez JWT: `401`; zwykły użytkownik: `403`. Administrator widzi zadania
wszystkich kont. Endpoint służy wyłącznie do odczytu.

Domyślnie lista obejmuje `queued`, `running`, `failed`, `needs_review`.
Opcjonalne filtry: `status` (również `succeeded`), `kind` (np. `story`, `render`),
`video_id`. Paginacja `limit=20` (1–100), `offset=0` (>=0), sortowanie od
najnowszych po czasie i ID. Odpowiedź:

- `items`, `total`, `limit`, `offset` — strona wyników;
- `counts` — liczby dla wszystkich statusów, respektujące `kind` i `video_id`,
  ale niezależne od wybranego filtra statusu.

Element zawiera ID zadania/właściciela/kanału/filmu/sceny, kolejkę, rodzaj,
status, próby i ich limit, znaczniki czasu, `request_id`, bezpieczny kod błędu
oraz kategorię. Nie zawiera `parameters`, `checkpoint`, `result`, promptów,
kluczy idempotency ani danych dostępowych.

```sh
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  'http://localhost:8000/api/v1/admin/jobs?status=failed&limit=20'
```

Aktualizacja: `docker compose up -d --build` wykona migrację `0017`.
Nie dodano zewnętrznego monitoringu ani zależności telemetrycznych. Punkty
rozszerzeń to middleware HTTP, kontekst logów, opakowanie providera i zdarzenia
workera. Etap 20 zamyka numerowaną listę etapów; pozostałe prace produktowe
(np. automatyczne połączenie całego pipeline i seed/demo) są wymienione w TODO.
