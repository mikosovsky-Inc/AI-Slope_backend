# AI-Slop backend

FastAPI + PostgreSQL, schematy API Pydantic, konfiguracja `pydantic-settings`,
hasła Argon2id i tokeny dostępu JWT (HS256). Modele bazy i sesje korzystają z SQLModel (opartego na SQLAlchemy i Pydantic),
a migracje z Alembic. E-maile są zapisywane małymi literami i unikalne.

## Stan projektu — etapy 1 i 2

Działający fundament modularnego monolitu: FastAPI, PostgreSQL, SQLModel,
Alembic, Redis, kontenery, health checks, logi JSON i obsługa błędów.
Istniejące rejestracja, logowanie JWT i role pozostają dostępne.
Etap 2 dodaje kanały, blueprint i filary treści. Integracje AI i generowanie
filmów nie są jeszcze wdrożone.
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


## TODO po etapie 6

- Etap 7: domena video, state machine i tworzenie video z zatwierdzonego pomysłu.
- Etapy 8–10: scenariusze, research faktów i reżyseria.
- Etapy 11–16: SeaweedFS, adaptery GPU/TTS, Dramatiq, FFmpeg i kontrola jakości.
- Etapy 17–20: scheduler, panelowe API, budżety i rozszerzona obserwowalność.

Na tym etapie nie ma workerów ani schedulera do uruchomienia, pełnego video
workflow ani polecenia seed/demo. Adapter OpenAI jest używany przez jawną analizę kanału. Sam start w trybie
`live` nie wykonuje płatnych operacji.


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
approved i rejected do czasu `used`. Status used jest zarezerwowany dla tworzenia
video w etapie 7; oba endpointy decyzji odrzucają wtedy zmianę z 409.

Nazwa filaru jest snapshotem: ponowna analiza kanału nie usuwa pomysłów.
Zapis całej paczki jest atomowy i sprawdza zmianę kanału po wywołaniu LLM.
Konflikt daje 409; błędy LLM 422/502/503, brak lub cudzy zasób 404.
Tryb mock daje jawne, deterministyczne przykładowe pomysły offline. Live korzysta
z istniejącego OpenAIProvider; każde generowanie jest nową, potencjalnie płatną akcją.

Migracja `0005` tworzy `content_ideas`, ograniczenia statusów/formatów i indeksy.
Compose stosuje ją przy starcie. Lokalnie: `uv run --no-active alembic upgrade head`.
