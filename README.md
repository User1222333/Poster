# Marketplace Data Bridge

Deterministyczne (bez AI) przekształcanie katalogów produktów do szablonów marketplace opartych na Mirakl
(Empik, Media Expert, Castorama). Aplikacja nie zna z góry kolumn ani słowników żadnego rynku — źródłem prawdy
jest szablon wgrany przez sprzedawcę. Brakujące dane nigdy nie są wymyślane: kończą się zgłoszeniem.

Nie jest to system zamówień, WMS ani integrator API. Wynikiem jest plik, który sprzedawca sam wgrywa na rynek.

## Uruchomienie lokalne (bez Supabase)
Wymagania: Python 3.12, Node 22, Postgres 16.

```bash
createdb mdb    # lub: docker compose up db
```
```bash
cd worker && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```
```bash
export DATABASE_URL=postgresql://mdb:mdb@localhost:5432/mdb AUTH_MODE=local INLINE_WORKER=1 \
  JWT_SECRET=dev-secret-dev-secret-dev-secret-0000 && .venv/bin/python -m app.migrate --local-shim \
  && .venv/bin/uvicorn app.api:app --port 8000
```
```bash
cd web && npm install && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
```
Panel: http://localhost:3000 → „Zarejestruj się” → „Nowy projekt”. Alternatywnie `docker compose up` (Postgres + API + worker).

## Uruchomienie z Supabase
1. Wgraj `supabase/migrations/0001_init.sql` (`supabase db push` lub SQL Editor). **Nie** używaj `--local-shim`.
2. Worker: `AUTH_MODE=supabase`, `STORAGE_MODE=supabase`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`
   (rola `postgres`, session pooler). Starsze projekty z HS256: dodatkowo `JWT_SECRET`.
3. Panel (np. Vercel): `NEXT_PUBLIC_AUTH_MODE=supabase`, `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`.
4. API i worker (`python -m app.worker`) uruchom jako długo żyjące procesy poza Vercel; `CORS_ORIGINS` = adres panelu.

Pełna lista zmiennych: `.env.example`.

## Testy
```bash
cd worker && createdb mdb_test; .venv/bin/pytest && .venv/bin/ruff check app tests
```
```bash
cd web && npm run typecheck && npm run lint && npm run build
```
```bash
cd web && npm run e2e     # wymaga działającego API (AUTH_MODE=local, INLINE_WORKER=1) i panelu
```

## Test na własnych danych (eksport Sellasist + szablon Mirakl)
1. **Źródło** — wgraj eksport CSV/XLSX. Sprawdź separator, arkusz i wiersz nagłówka w podglądzie.
2. **Marketplace i szablon** — wybierz rynek i wgraj szablon pobrany z panelu sprzedawcy dla swojej kategorii.
   W „Strukturze” wskaż: arkusz danych, wiersz z **kodami** kolumn, pierwszy wiersz danych, kolumnę kategorii,
   ewentualnie arkusz słowników i arkusz opisu atrybutów. W „Profilu wymagań” uzupełnij z dokumentacji rynku to,
   czego nie dało się odczytać (wymagalność, typy, limity, zakazane zwroty, dozwolony HTML). Zatwierdź.
3. **Rozpoznanie kolumn** — popraw propozycje, zapisz profil importu (np. „Eksport Sellasist firmy”).
4. **Kategorie i reguły** — zatwierdź przypisania kategorii, ustaw źródła i transformacje, sprawdź podgląd, zapisz preset.
5. **Walidacja** — przejrzyj grupy problemów, uzupełnij słownik wartości, popraw dane pojedynczo lub masowo.
6. **Pobierz plik** — „tylko gotowe” lub „pełny plik”; pobierz też raport braków. Otwórz plik i porównaj z szablonem,
   zanim wgrasz go na rynek. Przy kolejnym pliku użyj zapisanego profilu importu i presetu reguł.

## Ograniczenia
Patrz `docs/PROGRESS.md` („Nie zrobione / nie zweryfikowane”). Najważniejsze: zgodność z rzeczywistym szablonem Mirakl
nie została jeszcze sprawdzona na żadnym prawdziwym pliku, a marketplace może stosować dodatkową weryfikację katalogową.

## Struktura
- `worker/app/engine/` — silnik: `source_parser`, `template_parser`, `aliases`, `canonical`, `rules`, `validate`, `exporter`, `normalize`, `gtin`
- `worker/app/` — `api` (FastAPI), `pipelines` + `jobs` (kolejka), `auth`, `db`, `storage`, `migrate`, `worker`
- `worker/fixtures/` — dane demonstracyjne i **fikcyjny** szablon testowy
- `supabase/migrations/` — schemat i RLS; `worker/dev/local_auth_shim.sql` — tylko dev
- `web/` — panel (Next.js); `web/e2e/` — test kreatora
- `docs/` — plan i postęp
