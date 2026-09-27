# Architektura — Foundation i Channel Domain

## Zakres i granice

Backend jest modularnym monolitem FastAPI. Frontend pozostaje osobną aplikacją
HTML/CSS/JS hostowaną przez własny FastAPI. Nie wprowadzamy mikroserwisów,
Kubernetes ani zmian istniejącego auth. Zachowujemy działający punkt wejścia
`main:app`, SQLModel, migracje `0001` i `0002`, JWT i role.

```text
Przeglądarka → frontend :3000 → API :8000
                                 ├── PostgreSQL :5432
                                 └── Redis :6379
```

Compose etapu 1 obejmuje API, PostgreSQL i Redis. Własne wolumeny przechowują dane
obu usług. Redis działa z AOF; w tym etapie służy do weryfikacji infrastruktury.
Nie jest jeszcze brokerem działającego workflow.

## Organizacja kodu

- `main.py`: start/stop, składanie routerów, CORS i obsługi błędów.
- `app/core`: konfiguracja Pydantic Settings, logging, Redis, bezpieczeństwo, błędy.
- `app/api/routes`: cienkie endpointy auth, `/health` i `/ready`.
- `app/services`: logika auth i weryfikacja zależności.
- `app/models`: tabele SQLModel; `app/schemas`: kontrakty Pydantic API.
- `app/db`: engine i sesja na request; `migrations`: Alembic.
- `tests/unit`, `tests/integration`: izolowane testy oraz rzeczywiste PostgreSQL/Redis.

Istniejące puste pliki przyszłych funkcji nie są implementacją modułów.
Moduł kanałów działa w `app/modules/channels`; kolejne moduły
będą powstawać w `app/modules/<domena>`; nie przenosimy
teraz działającego auth wyłącznie dla zmiany układu folderów.

## Konfiguracja i cykl życia

Zmienne procesu > `.env` > wartości domyślne. Walidacja wymaganych ustawień
zatrzymuje start przy błędnej konfiguracji. Sekret JWT nie jest wbudowany w obraz.
`EXTERNAL_PROVIDERS_MODE` jest enumem literalnym `mock | live`, domyślnie `mock`.
Do etapu integracji obie wartości są nieaktywne funkcjonalnie.

API ma pool PostgreSQL z kontrolą połączeń i timeoutami. Klient Redis powstaje
na czas życia aplikacji i zamyka się podczas shutdown; połączenie jest leniwe.
Kontener najpierw wykonuje migracje, następnie przez `exec` uruchamia Uvicorn.
`init: true` i okres zamknięcia pozwalają dostarczyć sygnały do procesu API.
Migracje przy starcie są przeznaczone dla jednej lokalnej instancji API.

## Health i błędy

`GET /health` jest liveness: proces obsługuje HTTP, odpowiedź `{"status":"ok"}`.
`GET /ready` jest readiness: SELECT 1 + Redis PING. Sukces daje tę samą odpowiedź,
awaria zależności daje 503 i Retry-After. Żaden endpoint nie ujawnia DSN, haseł
ani danych użytkowników. Docker sprawdza `/ready`.

Walidacja nadal daje 422 bez odbijania danych wejściowych (np. hasła).
Błędy SQLAlchemy/Redis dają bezpieczne 503, pozostałe nieobsłużone błędy 500.
Istniejące błędy auth 401 i 409 pozostają bez zmian. Sesja DB zamyka transakcję
po zakończeniu requestu. Rejestracja jawnie zatwierdza lub wycofuje zapis.

Logi `app.*` mają format JSON (UTC, level, logger, message, opcjonalnie error_type).
Handlery błędów nie logują surowych komunikatów wyjątków, SQL ani request body.
Logi Uvicorn zachowują standardowy format. Request IDs, kontekst job/video,
timing i provider latency należą do etapu 20.

## Wybór kolejki na przyszłość

Wybór: **Dramatiq + Redis**. Dla początkowego monolitu zapewnia prosty model
aktorów, obsługę Redis i retry z exponential backoff bez konfiguracji pełnego
workflow Celery. Trwały stan zadań, idempotency i recovery będą nadal w PostgreSQL;
retry brokera nie zastępuje transakcji domenowych. Implementacja w etapie 14.
Scheduler stanie się osobnym procesem monolitu w etapie 17.

Źródło: [Dramatiq User Guide](https://dramatiq.io/guide.html).

## Integracje i storage w kolejnych etapach

Integracje OpenAI, ElevenLabs, Runpod i storage będą miały interfejsy oraz
implementacje mock/live poza logiką domenową. Nowy adapter będzie implementował
kontrakt danego providera i otrzymywał konfigurację przez dependency injection.
Nie dodajemy pustej uniwersalnej abstrakcji providerów w etapie 1.

Docelowy storage: SeaweedFS przez zgodne z S3 API, nie PostgreSQL. Bardziej
szczegółowe wymaganie SeaweedFS ma pierwszeństwo przed wzmianką MinIO w ogólnym
opisie lokalnego uruchomienia. Storage pojawi się w etapie 11.

## Kolejne kroki

Etap 2 ukończony: modele kanału, blueprint i pillars, walidacja, indeksy, CRUD i ownership.
Następny etap 3: LLMProvider i OpenAIProvider ze structured outputs i trybem mock.
Kolejne etapy obejmą cały pipeline TOP5/STORY. W etapie 1 nie ma pipeline video,
renderera, fake filmów ani automatycznej publikacji.

## Weryfikacja etapu 1 — 2026-09-27

- `docker compose up --build -d --wait`: API, PostgreSQL i Redis zdrowe.
- 47 testów przeszło, w tym 4 testy integracyjne na PostgreSQL/Redis; bez pominięć.
- Lint Ruff, formatowanie i `git diff --check` poprawne.
- Działający kontener: health, readiness, rejestracja administratora, login i `/me`.
- Zatrzymanie Redis: `/ready` → 503, `/health` → 200.
- Przywrócenie Redis: `/ready` → 200 bez restartu API.
- Restart API: ponowne migracje bez błędu, konto zachowane, logowanie działa.

Weryfikacja korzystała z oddzielnego projektu Compose `ai-slop-foundation-check`,
nowych wolumenów i portów 18080/15432/16379. Istniejące dane użytkownika nie były
używane w testach. Etap 2 nie został rozpoczęty.


## Etap 2 — Channel Domain

`app/modules/channels` zawiera modele SQLModel, kontrakty Pydantic oraz serwis.
Router wywołuje serwis i nie implementuje logiki domenowej. Rejestr modeli
`app.models` udostępnia ich metadane Alembic. Migracja `0003` nie zmienia użytkowników.

Relacje i usuwanie:

```text
User 1 → N Channel 1 → 1 ChannelBlueprint 1 → N ContentPillar
```

FK mają `ON DELETE CASCADE`. Unikalne channel_id gwarantuje jeden blueprint na kanał,
a para blueprint_id/position — jedno miejsce filaru w kolejności.
Composite index owner_id/created_at/id obsługuje listy właściciela;
index status przygotowuje wybieranie aktywnych kanałów.
Enumy status/autopilot są ograniczone także przez CHECK w PostgreSQL,
podobnie język, częstotliwość i budżet. Kwoty to NUMERIC(10,4)/Decimal.

JWT ustala owner_id. Każda operacja pobiera kanał razem z warunkiem właściciela.
Nie ma wyjątku dla administratora. Kanał obcy i nieistniejący mają tę samą odpowiedź 404.
Modyfikacje blokują wiersz kanału (`FOR UPDATE`) do końca transakcji.
Odczyty nie zakładają blokady wiersza. Zmiana blueprintu zastępuje konfigurację
oraz filary w tej samej transakcji co zmiana kanału; rollback chroni przed
częściową aktualizacją. Usunięcie wykorzystuje kaskady bazy.

W etapie 2 blueprint jest początkową konfiguracją, nie analizą AI. Wspólne parametry
(opis, język, częstotliwość, budżet) pozostają w Channel, aby nie przechowywać dwóch
rozbieżnych wartości. Typed configuration zawiera tone, audience, format mix,
video style i visual style. Etap 4 rozszerzy ją o rezultaty intelligence.

Statusy: draft po utworzeniu, active po activate, paused po pause. Akcje można
powtarzać bez zmiany updated_at; pause jest dozwolone również dla draft.
Manual i semi_auto są zapisanym wyborem; wykonanie workflow pojawi się później.

Testy obejmują walidację, auth, ownership, CRUD na PostgreSQL, trwałość blueprintu,
kaskady, idempotentne akcje, rollback błędu zapisu filaru, ograniczenia DB oraz
zgodność migracji z metadanymi modeli i downgrade/upgrade.

### Weryfikacja etapu 2 — 2026-09-27

76 testów przeszło bez pominięć, z PostgreSQL i Redis. Ruff i formatowanie poprawne.
Pełny stack zbudowano i uruchomiono w osobnym projekcie Compose.
Test HTTP kontenera objął auth, tworzenie/listowanie/odczyt kanału, zmianę blueprintu
z filarami, aktywację, pauzę i usunięcie. Dane testowe były w osobnych wolumenach.
Etap 3 nie został rozpoczęty.
