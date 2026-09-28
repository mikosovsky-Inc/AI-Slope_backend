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
Etap 3 opisano poniżej.
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
Etap 2 zakończony.


## Etap 3 — granica integracji LLM

`app/shared/llm.py` zawiera kontrakt Protocol i modele Pydantic request/result/usage.
`app/integrations/llm/openai.py` jest jedynym miejscem importu SDK OpenAI.
Adapter używa Responses API ze schematem Pydantic i strict structured output.
Factory wybiera mock/live na podstawie pydantic-settings. FastAPI zarządza cyklem
życia klienta, a zależność `CurrentLLM` udostępnia kontrakt przyszłym endpointom.

Mock jest deterministyczny, waliduje jawnie zarejestrowane fixture i nie otwiera
połączeń. Schematy konkretnych odpowiedzi domenowych i fixture analizy kanałów
powstaną w etapie 4 wraz z konsumentem kontraktu. Nie dodano migracji ani endpointów;
obecne zabezpieczenia JWT/ownership pozostają aktywne.

SDK realizuje ograniczone retries i timeout HTTP. Nie ma ponownego generowania
po błędnej walidacji. Błędy integracji są tłumaczone na bezpieczne błędy kontraktu.
Usage pozostaje metadanymi wyniku; trwałe CostEvent i budżetowanie są później.
Testy transportu sprawdzają parser, schemat strict, brak store, tokeny, odmowę,
niepełne odpowiedzi, walidację, timeout/recovery i limity retries. Test importów
pilnuje granicy SDK. Nie wykonano płatnych wywołań OpenAI.

### Weryfikacja etapu 3 — 2026-09-27

92 testy przeszły bez pominięć z PostgreSQL i Redis, w tym 16 nowych testów LLM.
Ruff i formatowanie poprawne. Obraz API zbudowany, wszystkie trzy serwisy
osobnego stosu Compose osiągnęły healthy. Wywołania OpenAI testowano wyłącznie
przez symulowany transport HTTP; live API nie było wywoływane.
Następny etap: Channel Intelligence Service (etap 4).


## Etap 4 — Channel Intelligence

Moduł `intelligence` składa prompt z idea/language i wywołuje kontrakt LLMProvider.
Pydantic `ChannelAnalysis` wymaga kompletnej odpowiedzi; schematy odpowiedzi AI
nie mają domyślnych wartości maskujących brakujące pola. Schemat odczytu starych
blueprintów zachowuje kompatybilność. Rozszerzamy JSON konfiguracji, bez zmian tabel.

Endpoint `POST /api/v1/channels/{id}/analyze` wymaga CurrentUser oraz ownership.
Warstwa route nie zawiera orkiestracji. Przed wywołaniem providera kończymy odczytową
transakcję. Po odpowiedzi ponownie pobieramy kanał z FOR UPDATE i porównujemy
updated_at. Wszystkie istniejące modyfikacje kanału/blueprintu aktualizują ten znacznik.
Konflikt kończy się 409, usunięty/cudzy kanał 404. Zastąpienie blueprintu i filarów
jest atomowe; błąd providera lub DB nie niszczy poprzedniego wyniku.

Mock ma jawną funkcję fixture dla ChannelAnalysis (pl/en), oznaczoną jako przykład
offline. Nie wykonujemy web search. Osobny CompetitorResearchProvider przyjmuje
seed keywords i zwraca typed records; LocalCompetitorResearchProvider filtruje dane
przekazane przez wywołującego. Wyszukiwarka i trwały zapis konkurentów to etap 5.

Analiza jest jawną akcją po utworzeniu kanału, by nie wywoływać płatnego modelu
przy każdym POST /channels. Częstotliwość sugerowana przez AI pozostaje sugestią;
rzeczywista częstotliwość, budżet i język nadal mają jedno źródło prawdy w Channel.
Nie uruchamiamy generowania filmów ani schedulera. Brak trwałego joba analizy jest
świadomym ograniczeniem do etapu 14; błędy są raportowane HTTP i bezpiecznym logiem.

### Weryfikacja etapu 4 — 2026-09-27

109 testów przeszło bez pominięć z PostgreSQL i Redis, w tym 17 nowych testów.
Testy PostgreSQL potwierdzają atomowy rollback błędu zapisu filaru i konflikt
równoległych analiz (jedna 200, druga 409). Ruff i formatowanie poprawne.
Osobny stack Docker osiągnął healthy. Test HTTP: docs, readiness, rejestracja,
logowanie, utworzenie kanału, ochrona analizy przez JWT, analiza i trwały odczyt.
Testy korzystały wyłącznie z mocka; nie wykonano płatnych wywołań OpenAI.
Następny etap: Competitor Research (5).

## Etap 5 — zapis benchmarków konkurencji

Moduł competitors używa istniejącego CompetitorResearchProvider. Encje SQLModel
Competitor i CompetitorContent są objęte migracją 0004. URL identyfikuje konkurenta
w obrębie kanału; wpisy różnych właścicieli nie są współdzielone. Przykładowe tytuły
są oddzielnymi rekordami, bez kopiowania pełnych treści. FK CASCADE usuwa benchmarki
wraz z kanałem i tytuły wraz z konkurentem.

POST competitor-research zwalnia transakcję przed providerem. Po walidacji całej
paczki blokuje kanał, sprawdza wersję updated_at i wykonuje atomowy upsert wraz
z zastąpieniem tytułów. Równoległe zmiany powodują 409 zamiast cichego nadpisania.
Pusty wynik nie kasuje historii. GET jest stronicowany i sprawdza ownership.

Obecny lokalny provider domyślnie nie ma rekordów. To świadome ograniczenie:
brak skonfigurowanej wyszukiwarki nie jest zastępowany fikcyjnymi konkurentami.
Integrację można wstrzyknąć przez zależność; logika domenowa nie pobiera URL.

### Weryfikacja etapu 5 — 2026-09-27

117 testów przeszło bez pominięć z PostgreSQL i Redis. Weryfikacja obejmuje
migrację i zgodność metadanych, upsert bez duplikatów, pusty wynik, pagination,
auth/ownership, walidację wyników, konflikt edycji, rollback błędu zapisu tytułu
i kaskadowe usuwanie. Ruff oraz formatowanie poprawne. Test HTTP osobnego stosu
Docker przeszedł: readiness/docs, auth, kanał, analiza, research i lista.
Brak płatnych calli i wyszukiwania w sieci. Następny etap: Idea Engine (6).

## Etap 6 — Idea Engine

ContentIdea (SQLModel, migracja 0005) ma enum statusów candidate/approved/rejected/used,
enum formatu top5/story, snapshot nazwy filaru oraz zwalidowane heurystyki JSON.
Kanał jest właścicielem historii przez FK CASCADE. Unikalność channel_id/title_key
ogranicza dokładne duplikaty po normalizacji Unicode i białych znaków.

Moduł ideas składa ograniczony kontekst z blueprintu, filarów, benchmarków, języka
i poprzednich tematów, po czym zwalnia transakcję przed LLM. Zwalidowaną paczkę
zapisuje pod blokadą kanału, sprawdzając updated_at i duplikaty w pełnej historii.
Zmiana kanału/analizy/researchu/decyzji pomysłu w trakcie generowania powoduje 409.
Równoległe generowania nie nadpisują się. Błąd pojedynczego zapisu wycofuje paczkę.

Route handlers delegują do serwisu. Każda akcja wymaga JWT i ownership.
Approve/reject są idempotentne, decyzje można zmieniać przed used. Tworzenie filmów
i przejście do used nie należą do etapu 6. Oceny nie są prognozami popularności.
Brak podobieństwa semantycznego i ograniczony kontekst historyczny są jawnymi
ograniczeniami tej wersji. Nie kopiujemy tytułów benchmarków z przekazanego kontekstu.

### Weryfikacja etapu 6 — 2026-09-27

130 testów przeszło bez pominięć z PostgreSQL i Redis (13 nowych).
Sprawdzono auth/ownership, pl/en, listę i filtry, decyzje idempotentne, ochronę used,
walidację paczki, duplikaty, historię i benchmarki w promptach, rollback, cascade,
zgodność migracji z metadanymi i konkurencję (jedna paczka 201, druga 409).
Ruff i formatowanie poprawne. Test HTTP osobnego stosu Docker: tworzenie kanału,
analiza, generowanie 20 pomysłów, lista, approve, reject. Wyłącznie mock, bez
płatnych wywołań. Następny etap: Video Domain (7).

## Etap 7 — Video Domain

Video odwołuje się do ContentIdea przez unikalny idea_id, a własność i kanał
wynikają z idei. Nie duplikujemy channel_id w tabeli Video. Blueprint i budżet
są historycznym snapshotem; zmiany kanału nie modyfikują już utworzonego filmu.
VideoScript ma unikalny video_id, Scene unikalną parę script_id/position.
Scene ma Numeric duration i enum typu wizualnego. Migracja 0006 dodaje te encje
oraz VideoStatusEvent z enumami statusów i unikalną sekwencją per video.

Create-video wymaga JWT i właściciela. Pobiera blokadę kanału (ta sama kolejność
co approve/reject), ponownie odczytuje pomysł i sprawdza istniejący film. Tworzenie,
przejście DRAFT → IDEA_GENERATED, historia i oznaczenie idei used są atomowe.
Ponowienie zwraca istniejący rekord, także po późniejszych zmianach stanu.

Maszyna stanów jest oddzielną funkcją domenową; TOP5 nie pomija researchu,
STORY może wejść bezpośrednio w SCRIPTING. Wewnętrzna operacja transition_video
blokuje wiersz Video, odczytuje aktualny status i dopisuje sekwencyjne zdarzenie.
Wywołujący zarządza commit/rollback, żeby status i efekt przyszłego kroku workflow
(np. zapis skryptu) mogły stanowić jedną transakcję. Brak publicznego endpointu
arbitralnej zmiany stanu. FAILED przechowuje bezpieczny powód w historii;
retry/recovery wymaga osobnego projektu z jobami, a nie swobodnego cofania stanów.

Workflow w etapie 7 zatrzymuje się trwale na IDEA_GENERATED. Nie udajemy wykonania
researchu/scenariusza/renderu. Generatory oraz workery zostaną dołączone w swoich
etapach. Maszyna stanów określa legalną kolejność; walidację artefaktów poszczególnych
kroków zapewnią ich serwisy. Skrypty i sceny nie są jeszcze tworzone przez endpoint.

### Weryfikacja etapu 7 — 2026-09-27

139 testów przeszło bez pominięć z PostgreSQL i Redis (9 nowych).
Testy obejmują maszynę stanów TOP5/STORY, idempotencję tworzenia i przejść,
auth/ownership, snapshoty, used, rollback historii i pomysłu, równoległe żądania
(jedno 201, drugie 200, jeden film), kaskady, ograniczenia DB i zgodność migracji
z metadanymi. Ruff i formatowanie poprawne. Test HTTP izolowanego stosu Docker:
rejestracja, kanał, analiza, pomysły, approve, create-video, ponowienie, ochrona used.
Brak płatnych wywołań i generowania scenariuszy/mediów. Następny etap: STORY Script Engine (8).

## Etap 8 — STORY Script Engine

Moduł scripts korzysta wyłącznie z LLMProvider. Trzy wymagane schematy Pydantic
opisują zarys, pełną historię i sceny. Narracja ma pięć obowiązkowych części:
hook/setup/escalation/reveal/twist. Migracja 0007 utrwala outline/story w VideoScript.
Sceny zachowują uniwersalny format modelu z etapu 7.

POST /videos/{id}/script/generate sprawdza ownership i format, następnie pod blokadą
Video zmienia IDEA_GENERATED → SCRIPTING i zatwierdza transakcję. Inny request widzi
SCRIPTING i zwraca 409. Po wywołaniach LLM zapisuje skrypt, sceny i SCRIPT_READY
w jednej transakcji. Odczyt/replay gotowego skryptu nie generuje kosztów.
Sceny muszą zachować całą narrację, język/tytuł/duration celu, pierwszą scenę hook
oraz sumę czasu w tolerancji 10%. Nie jest to pomiar audio ani gwarancja spójności
literackiej; takie oceny wymagają kolejnych etapów jakości.

Przy błędzie cofamy zapis i próbujemy trwale ustawić FAILED z bezpiecznym kodem.
Brak możliwości zapisu błędu jest logowany. Nie ma automatycznego resume po śmierci
procesu; SCRIPTING może wymagać recovery przewidzianego z trwałymi jobami w etapie 14.
Obecny request jest synchroniczny, a status blokuje duplikaty bez utrzymywania
transakcji podczas LLM. Nie wykonujemy TOP5 bez researchu i nie generujemy mediów.

### Weryfikacja etapu 8 — 2026-09-28

151 testów przeszło bez pominięć z PostgreSQL i Redis (12 nowych). Sprawdzono
PL/EN, przekazywanie wyników między trzema krokami, pełny zapis i replay,
auth/ownership, blokadę TOP5, walidację narracji/czasu/hooka/kolejności,
FAILED po błędach, brak częściowych skryptów, równoległy request 409 oraz rollback
błędu zapisu scen na PostgreSQL. Migracje odpowiadają metadanym. Ruff i formatowanie
poprawne. Test HTTP kontenera przeszedł od kanału i idei do skryptu, odczytu,
replay i SCRIPT_READY. Wyłącznie mock, bez płatnych calli. Następny etap: TOP5 Research Engine (9).
