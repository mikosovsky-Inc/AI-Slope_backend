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

## Etap 9 — research TOP5 i przypisanie źródeł

ResearchProvider jest oddzielony od LLMProvider i od benchmarków konkurencji.
Lokalny adapter pracuje na jawnym korpusie, domyślnie pustym. Zewnętrznej wyszukiwarki
nie podłączono. Moduł research generuje zapytania, waliduje dokumenty i wymaga
cytatów występujących dosłownie w przekazanym źródle. Nie akceptuje URL/tytułów
wymyślonych przez ekstraktor — przypisuje je z dokumentów po zweryfikowanym ID.

Migracja 0008 dodaje ResearchDocument, ResearchFact i SceneResearchFact. Własność
wynika z Video → ContentIdea → Channel. Dokumenty są unikalne per video/URL,
fakty deduplikowane po znormalizowanej treści, cytowania wskazują konkretne sceny.
Dla kontynuacji potrzeba 5 faktów o confidence >= .7 z minimum 2 dokumentów.
Nie oznacza to niezależnego fact-checkingu; confidence jest heurystyką modelu.

Research i zapis scenariusza to osobne jawne akcje. Każda claimuje status filmu
pod FOR UPDATE, zwalnia transakcję przed providerem i zapisuje efekt atomowo.
Niewystarczające poprawne dane pozostają do audytu przy FAILED. Nieprawidłowe cytaty
powodują odrzucenie paczki. Skrypt wybiera 5 ID tylko z researchu danego Video;
serwer wstawia dokładne statements jako narrację. Model tworzy wyłącznie kolejność,
czasy i prompty. Zapis skryptu, scen, cytowań i SCRIPT_READY to jedna transakcja.
Ukończone operacje są odczytywane bez ponownego generowania. Błędy zachowują research,
ale nie częściowy skrypt. Wspólny GET /script zwraca wariant STORY lub TOP5.

### Weryfikacja etapu 9 — 2026-09-28

163 testy przeszły bez pominięć z PostgreSQL i Redis (12 nowych). Sprawdzono
pełny research → fakty → TOP5 → cytowania → SCRIPT_READY na jawnym korpusie testowym,
replay, auth/ownership, niewystarczające źródła, wymyślone cytaty/ID, niepoprawny plan,
czas, konkurencję, rollback błędu zapisu cytowania i kaskady. Migracje są zgodne
z metadanymi. Ruff i formatowanie poprawne. Docker osiągnął healthy; test HTTP
potwierdził 422 i FAILED dla pustego korpusu oraz brak możliwości wygenerowania
scenariusza bez źródeł. Nie wykonano realnego web search ani płatnych calli.
Następny etap: Director (10).

## Etap 10 — Director

DirectorService wybiera image/video i aktualizuje prompty, styl, camera_motion,
importance i generation_priority. Jest deterministyczny; nie korzysta z LLM.
Heurystyka bazuje na pozycji i czasie sceny, a styl/udział video na snapshotcie
blueprintu. Oba formaty korzystają z tego samego modelu Scene. Narracja i źródła
TOP5 nie są modyfikowane. Kolejność produkcji jest oddzielona od kolejności odtwarzania.

VisualCostEstimator oddziela estymację cen od domeny. ConfiguredVisualEstimator
czyta stawki z pydantic-settings; domyślne wartości są przykładowe, nie cennikiem.
Budżet wizualny jest częścią budżetu filmu (domyślnie .5), a cap scen video wynika
wyłącznie z blueprintu. Najpierw image-only baseline, potem upgrade wg importance
przy zachowaniu cap i budżetu. Nie dopuszczamy do nieskończonych retries/generowań,
bo ta operacja nie generuje assetów i nie wykonuje żadnych zewnętrznych calli.

Migracja 0009 dodaje DirectorPlan (unikalny video_id, stawki i koszt szacunkowy)
oraz kolumny scen z ograniczeniami DB. Priorytet jest unikalny w skrypcie, importance
mieści się w [0,1]. Plan i aktualizacje scen to jedna transakcja blokująca Video.
Powtórzenie/równoległe wywołanie odczytuje ten sam plan. Status pozostaje SCRIPT_READY.
Brak planu/niegotowy skrypt/niemożliwy baseline budżetu jest jawnie raportowany.
Kolejny etap doda assety i joby, dopiero potem realne generowanie.

### Weryfikacja etapu 10 — 2026-09-28

172 testy przeszły bez pominięć z PostgreSQL i Redis (9 nowych). Sprawdzono
konfigurowalny udział video, ograniczenie budżetu, wariant image-only, snapshot
blueprintu, auth/ownership, replay, zachowanie narracji/cytowań TOP5, konkurencję,
rollback, kaskady i zgodność migracji. Ruff i formatowanie poprawne.
Test HTTP kontenera: kanał → STORY → script → Director → budget → odczyt/replay.
Bez płatnych wywołań i generowania mediów. Następny etap: Asset Domain (11).

## Etap 11 — Asset Domain

Modele SQLModel Asset i GenerationJob oraz migracja 0010 przechowują wyłącznie
metadane. Asset wskazuje film, opcjonalną scenę i job; zawiera typ, lokalizację,
rozmiar i SHA-256. GenerationJob ma jawny enum statusów, parametry JSON, czasy,
retry_count oraz error przeznaczony na bezpieczny komunikat. Unikalny klucz
idempotencji w scenie i jeden Asset na job są podstawą przyszłych ponowień.
Ograniczenia DB pilnują poprawności statusów, czasów, rozmiaru i lokalizacji.

StorageProvider oddziela domenę od systemu plików i boto3. LocalStorageProvider
zapisuje atomowo w prywatnym katalogu i zwraca wewnętrzne file URI. S3StorageProvider
obsługuje SeaweedFS i prywatne buckety S3, podpisuje download URL z ograniczonym TTL,
stosuje path-style, timeouty i maksymalnie trzy próby SDK ze standardowym backoff.
Oba adaptery liczą SHA-256, ograniczają rozmiar strumienia i odrzucają niebezpieczne
klucze. Zapis pod istniejącym kluczem zastępuje obiekt; delete jest idempotentne.
S3 spool trafia na dysk po 1 MiB, a lokalny zapis używa pliku tymczasowego.

Konfiguracja i sekrety pochodzą z pydantic-settings. Lokalny storage jest domyślny
poza Compose; Compose dodaje SeaweedFS 4.47 mini, bucket i trwały volume.
Wewnętrzny endpoint służy operacjom SDK, publiczny endpoint podpisywaniu URL.
API tworzy adapter w lifespan i zamyka klientów SDK przy shutdown.
Nie ma nowych endpointów HTTP ani publicznego mounta plików. /ready nadal sprawdza
PostgreSQL i Redis; nie stanowi testu dostępności storage. Faktyczne operacje
storage są weryfikowane osobnym testem integracyjnym.

Etap dostarcza modele i storage; wykonanie jobów zacznie się przy adapterach
generowania i workerach. Przyszła usługa musi sprawdzić ownership oraz zgodność
film–scena–job, stosować stabilne klucze i obsłużyć kompensację po błędzie DB.
Nie istnieje transakcja obejmująca jednocześnie storage i PostgreSQL. Usunięcie
metadanych przez kaskadę nie usuwa obiektów; do workflow trzeba podłączyć sprzątanie
osieroconych plików. Same ograniczenia unikalności nie zastępują recovery workera.

### Weryfikacja etapu 11 — 2026-09-28

192 testy przeszły bez pominięć z PostgreSQL, Redis i SeaweedFS (20 nowych).
Sprawdzono roundtrip i podmianę pliku, SHA-256, limity rozmiaru, puste pliki,
path traversal, symlinki, sprzątanie plików tymczasowych, błędy SDK, parametry
podpisanych URL, prywatność bucketa, idempotentne delete, ograniczenia jobów,
unikalność wyników i kaskady. Istniejące testy migracji potwierdzają zgodność
z metadanymi i downgrade/upgrade. Ruff i formatowanie poprawne.
Docker uruchomił API jako healthy; kontrola HTTP potwierdziła health/ready/docs,
a kontrola z kontenera zapis, odczyt i usuwanie obiektu oraz publiczny host podpisu.
GitHub Actions otrzymał SeaweedFS i test integracyjny S3. Testowy stos jest izolowany
od danych użytkownika. Następny etap: Runpod Interface (12).

## Etap 12 — Runpod Interface

ImageGenerationProvider i VideoGenerationProvider definiują submit, status i cancel
na modelach Pydantic. Interfejsy nie zależą od httpx ani SDK dostawcy. Referencja
GenerationJobRef przechowuje nazwę providera, endpoint_id i job_id, dzięki czemu
zmiana konfiguracji nie przekieruje odczytu istniejącego zadania do innego endpointu.
ProviderJobStatus jawnie rozróżnia queued/running/succeeded/failed/cancelled/timed_out.
To status integracji, a nie automatyczna zmiana Video lub GenerationJob w DB.

MockRunpodProvider przechowuje stan w pamięci, deterministycznie symuluje przebieg
oraz anulowanie i deduplikuje request_id w danym typie generowania. Zmienione dane
pod tym samym ID są odrzucane. Wynik jest oznaczony mock:true, bez fikcyjnego URL
udającego istniejący plik. Mock nie produkuje mediów i nie przetrwa restartu.

RunpodProvider obsługuje queue-based HTTP API przez httpx. Wysyła zadanie i oddaje
sterowanie; nie blokuje requestu na czas generowania GPU. Osobne endpointy/model
IDs dla obrazów i video pochodzą z pydantic-settings, podobnie SecretStr API key,
timeout, polityka czasu wykonania i TTL. Model jest opcjonalną wskazówką dla własnego
workera. Nie zakładamy formatu gotowego modelu lub szablonu. Dane wejściowe mają
jawny kontrakt opisany w README; output pozostaje JSON-em konkretnego workera.
Przyszły kod zapisujący Asset musi zweryfikować wynik przed pobraniem mediów.

GET status ma ograniczone retries z exponential backoff. POST submit i cancel
nie są automatycznie ponawiane. Niejednoznaczny submit (transport/5xx/nieprawidłowa
odpowiedź) ma osobny błąd GenerationSubmissionUnknown; request_id w input nie jest
gwarancją idempotencji API dostawcy. Worker może deduplikować go na własnym poziomie.
Przyszły workflow musi zapisać stan wysyłania i referencję, rozstrzygać unknown oraz
obsłużyć odzyskiwanie po restarcie. Nie obiecujemy exactly-once wykonania GPU.

Limit requestu 256 KiB, limit JSON odpowiedzi 1 MiB, bez redirectów i bez pobierania
URL z wyniku. Surowe błędy dostawcy nie trafiają do bezpiecznego wyniku integracji.
Provider jest tworzony/zamykany w lifespan FastAPI. W live klucz/endpoint są wymagane
przy użyciu; inicjalizacja nie wykonuje płatnych operacji. Nie dodano endpointów HTTP
ani automatycznego przejścia SCRIPT_READY → GENERATING_ASSETS. Orkiestracja, trwałość
jobów, zapis assetów i recovery zostają na integrację workerów w etapie 14.

### Weryfikacja etapu 12 — 2026-09-28

229 testów przeszło bez pominięć z PostgreSQL, Redis i SeaweedFS, w tym 37 nowych.
Testy adaptera live używają httpx.MockTransport: kontrakt submit/status/cancel,
wybór endpointu/modelu, mapowanie statusów, limit danych, backoff, timeout,
nieponawianie submitu, niewłaściwe JSON/ID/status, bezpieczne komunikaty i brak
przekierowań. Testy mocka obejmują replay, konflikt danych, kolejność parametrów,
anulowanie i izolację zwracanych wyników. Sprawdzono też konfigurację z .env,
maskowanie klucza i lifecycle aplikacji. Ruff i formatowanie poprawne.
Nie wykonano rzeczywistej generacji GPU; zgodność konkretnego wdrożonego workera
wymaga późniejszego testu na skonfigurowanym endpoincie. Następny etap: ElevenLabs (13).

Kontener API osiągnął healthy. Końcowy smoke test potwierdził HTTP /health,
/ready i /docs oraz zakończenie obrazu i anulowanie video w trybie mock.
Po weryfikacji usunięto wyłącznie izolowany stos ai-slop-runpod-check.

## Etap 13 — TTS i ElevenLabs

TTSProvider przyjmuje typowany TTSRequest (tekst, voice_id, język, VoiceSettings)
i zwraca TTSResult z bajtami, MIME, opcjonalnymi alignment/normalized_alignment
oraz metadanymi użycia. Mock tworzy poprawny, cichy WAV 16 kHz z przybliżonymi
czasami; ElevenLabsProvider używa httpx, with-timestamps i MP3 44.1 kHz / 128 kbps.
Model, głos, timeout, limit odpowiedzi i wysyłanie language_code są konfigurowalne.
Sekret pochodzi z pydantic-settings. Adapter jest dostępny przez app.state.tts.

Usługa generate_scene_audio jest wewnętrzna i synchroniczna, przeznaczona do
podpięcia do workerów. Przed zewnętrznym wywołaniem sprawdza ownership i scenę,
blokuje Video oraz zapisuje running GenerationJob i CostEvent. Fingerprint wejścia
zapewnia replay gotowego wyniku; running/failed blokują automatyczne ponowienie.
Nie zmienia statusu Video — kolejność workflow zostaje dla etapu 14.

CostEvent (migracja 0011) zapisuje provider, operację, model, film, kanał, job,
estimated_cost_usd, nullable actual_cost_usd, metadane i czas. Osobny adapter pricing
liczy estymację z jawnej stawki operatora na 1000 znaków; brak stawki blokuje usługę
live. Zwrócone character-cost i request-id są zapisywane przed operacją storage.
Raportowana liczba znaków nie jest rachunkiem w USD; actual_cost_usd pozostaje null
w live, a w mock wynosi zero. Nie wdrażamy jeszcze silnika budżetów z etapu 19.

Audio jest zapisywane przez StorageProvider pod stabilnym kluczem joba. Asset
zawiera sumę kontrolną, rozmiar i timestampy; completion joba i Asset to jedna
transakcja DB. Błąd metadanych powoduje próbę usunięcia pliku, zachowując zapis
kosztu. Błąd providera pozostawia job failed i szacunek kosztu do rozstrzygnięcia.
Nie ma transakcji obejmującej system zewnętrzny i DB. Restart może pozostawić
running lub osierocony obiekt; dalsze recovery nie może ślepo powtarzać płatnego TTS.

Adapter nie ponawia POST automatycznie, ma ograniczony timeout i rozmiar odpowiedzi,
nie podąża za redirectami i zwraca bezpieczne błędy. Waliduje base64, podstawowy
nagłówek MP3 i tablice alignment. Pełne dekodowanie/quality control to późniejszy
etap. Nie dodano publicznych endpointów ani pobierania plików bez autoryzacji.

### Weryfikacja etapu 13 — 2026-09-28

255 testów przeszło bez pominięć z PostgreSQL, Redis i SeaweedFS (26 nowych).
Sprawdzono kontrakt ElevenLabs przez MockTransport, błędy/timeouty/limity, brak
ponowień, optional alignment/language, sekrety z .env, poprawny WAV, ownership,
replay, koszt szacowany vs actual, zachowanie użycia po błędzie storage, konkurencję,
rollback Asset ze sprzątaniem pliku, kaskady i migrację zgodną z SQLModel.
Ruff i formatowanie poprawne. Kontener API osiągnął healthy. Nie wykonano
płatnych wywołań ElevenLabs. Następny etap: Async Workers (14).

Końcowy test kontenera potwierdził HTTP /health, /ready i /docs oraz przepływ:
rejestracja → kanał → STORY → scena → mock TTS → WAV w SeaweedFS → CostEvent.
Ponowne wywołanie zwróciło ten sam Asset. Weryfikacja używała wyłącznie izolowanego
stosu ai-slop-tts-check, usuwanego po testach.

## Etap 14 — Async Workers

Wybrano Dramatiq 2.2 z Redis, ponieważ pasuje do istniejącego stosu i prostego
modelu workerów w monolicie. Kolejki content/research/image/video/audio/render/quality
są deklarowane jawnie. Render i quality są zarezerwowane dla następnych etapów.
API nie wykonuje operacji providerów w requestach w domyślnym trybie. Historyczne
bezpośrednie wywołania usług pozostają tylko w trybie TASKS_EAGER testów regresyjnych;
Compose wymusza false. Oddzielne testy weryfikują rzeczywisty kontrakt asynchroniczny.

Migracja 0012 dodaje SQLModel Task: właściciel, target, rodzaj, parametry, status,
wynik, checkpoint, attempts/max_attempts, run_token i czasy dostarczania/wykonania.
Task jest trwałym outboxem. API zapisuje zadanie i zwraca 202; dispatcher wysyła
jego UUID do Redis poza requestem. Awaria pomiędzy enqueue i commitem dispatchera
może duplikować wiadomość, ale nie gubi zadania. Oczekujące zadania są ponownie
wysyłane po okresie redelivery, także po utracie danych Redis. delivery_after
jest osobne od available_at, żeby samo wysłanie nie przesuwało terminu wykonania.

POST create-video zapisuje film, IDEA_GENERATED, zużycie pomysłu i pierwszy task
w tej samej transakcji. STORY uruchamia scenariusz i Directora; TOP5 research,
scenariusz ze źródeł i Directora. Child task powstaje razem z commitem zakończenia
rodzica. Pusty lokalny research nadal kończy TOP5 bez generowania fikcyjnych faktów.
Workflow zatrzymuje się na SCRIPT_READY. Audio można zlecić dla sceny; zapisuje
Asset i CostEvent. Zadania image/video uruchamiają i odpytują Runpod, zachowując
referencję i wynik JSON. Nie deklarują gotowości wizualnych assetów ani nie
materializują plików z dowolnych URL; integracja mediów pozostaje przed renderingiem.

Idempotency-Key jest unikalny per user i powiązany z targetem/parametrami. Odmienny
request pod tym samym kluczem daje 409. API sprawdza własność przed zwróceniem
istniejącego zadania. GET task i GET video tasks również wymagają JWT właściciela.
Worker sprawdza aktywność użytkownika. Wiadomość zawiera tylko ID, nie prompt czy sekret.

Advisory lock PostgreSQL na UUID zadania jest trzymany na osobnym połączeniu przez
całe wykonanie, także pomiędzy commitami usług. Zwolnienie następuje przy zakończeniu
lub utracie procesu/połączenia. Token wykonania chroni końcowy commit starej próby.
Zakończone zadania nie są ponawiane; odczyt zapisanego scenariusza/research pozwala
odzyskać wynik po awarii pomiędzy commitem domeny a zakończeniem taska. Audio korzysta
z wcześniejszej ochrony GenerationJob. Director jest lokalny i idempotentny.

Runpod zapisuje marker submitu przed wywołaniem i referencję po odpowiedzi. Znana
referencja umożliwia polling bez kolejnego submitu; błędy odczytu mają ograniczony
backoff i max_attempts, a polling całkowity deadline. Niepewne efekty LLM/TTS/submitu
po awarii trafiają do needs_review. Domyślne retries Dramatiq są wyłączone, żeby nie
omijały tej polityki. Brak jeszcze endpointu ręcznego zatwierdzenia recovery.
Nie ma gwarancji exactly-once u zewnętrznego dostawcy. Samo wygaśnięcie lease nie
uruchamia równoległego płatnego calla, jeśli poprzednia próba nadal ma blokadę.

Compose dodaje worker (1 proces, 4 wątki) i dispatcher (interwał domyślnie 2 s).
Oba używają tej samej konfiguracji co API i startują po jego gotowości. Tylko API
stosuje migracje. Shutdown workerów ma dłuższy grace period, a klienci providerów
są zamykani po zadaniu. Dispatcher zatrzymuje się po sygnale. /ready nadal sprawdza
PostgreSQL i Redis, nie obecność workerów; przy wyłączonym workerze API przyjmuje
pracę do trwałej kolejki.

### Weryfikacja etapu 14 — 2026-09-28

268 testów przeszło bez pominięć z PostgreSQL, Redis i SeaweedFS (13 nowych).
Testowano 202 bez wykonywania LLM w requestcie, ownership, idempotency-key,
atomiczny zapis video/outbox, awarię dispatchu, rzeczywisty worker Redis,
duplikaty/równoległość, recovery zapisanego skryptu, needs_review, checkpoint Runpod,
backoff odczytu, nieaktywnego właściciela, zadania audio i wizualne oraz łańcuch
STORY → Director. Migracja odpowiada metadanym SQLModel. Ruff i formatowanie poprawne.

Test całego Compose zatrzymał workera, przyjął trwałe zadanie przez API, następnie
uruchomił workera i potwierdził: analiza → pomysły → create-video 202 → automatyczny
scenariusz → Director → zadanie audio → Asset w S3. Replay zachował to samo zadanie.
Bez płatnych wywołań. Izolowany stos ai-slop-workers-check jest usuwany po testach.
Pozostajemy na etapie 14; renderer i kontrola jakości nie zostały zaimplementowane.
