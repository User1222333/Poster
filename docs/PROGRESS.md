# Postęp

## Etapy
- [x] **M1 Fundament** — schemat + RLS (`supabase/migrations/0001_init.sql`), organizacje/role, logowanie (Supabase Auth lub tryb lokalny), powłoka panelu, testy.
- [x] **M2 Wejście** — CSV (UTF-8/BOM, separator, starsze kodowanie po potwierdzeniu), XLSX (arkusz, wiersz nagłówka), dane bezstratnie jako tekst, podgląd, aliasy kolumn z konfliktami, profile importu, kontrola źródła.
- [x] **M3 Szablon** — przechowanie oryginału + SHA-256 + wersja, parser struktury, kreator niejednoznaczności, profil wymagań (odczyt / ręcznie), zatwierdzanie, wykrywanie elementów niezachowywanych bezstratnie.
- [x] **M4 Transformacja** — 15 typów kroków, wersjonowane reguły, słowniki kategorii i wartości per rynek, podgląd na 3 rekordach, presety, pochodzenie każdej wartości.
- [x] **M5 Walidacja i korekty** — BLOCKER/REVIEW/WARNING, centrum błędów z filtrami i szczegółami, korekty pojedyncze i masowe (podgląd + potwierdzenie liczby), historia i przywracanie, rewalidacja.
- [x] **M6 Eksport** — XLSX na oryginalnym szablonie, CSV, raport braków XLSX/CSV, liczniki, ochrona przed formułami, czas życia plików, deklaracja zgodności.

## Wyniki testów (uruchomione lokalnie)
- `worker`: `pytest` — 78 testów zielonych (63 silnik, 15 integracyjnych API + Postgres + RLS); `ruff check` czysto.
- Wydajność: 2000 wierszy — import + model kanoniczny + reguły + walidacja ok. 1,5 s, szczytowy RSS procesu testów ok. 130 MB, 0 utraconych wierszy, drugi przebieg identyczny.
- `web`: `tsc --noEmit`, `eslint`, `next build` czysto; E2E kreatora (Playwright) — 27 asercji zielonych.

## Nie zrobione / nie zweryfikowane
- **Brak testu na prawdziwym szablonie Mirakl** — żaden nie został dostarczony. Parser jest generyczny; układ rzeczywistego pliku trzeba potwierdzić w kreatorze struktury. Do tego czasu żaden eksport nie powinien być uznawany za zgodny z rynkiem.
- **Supabase nieuruchomione w tym środowisku**: weryfikacja JWT przez JWKS i `SupabaseStorage` są napisane, ale nieprzetestowane na żywym projekcie. RLS przetestowano na Postgresie 16 z shimem `auth`.
- **`docker compose` nieuruchomione** (brak demona Docker w środowisku budowy).
- Import XML — poza MVP. XLSM/XLS — jawnie odrzucane.
- Wymagalność wariantów — brak (model nie ma jeszcze grup wariantów).
- Zależność wymagań od kategorii — tylko pole `categories` w API profilu wymagań, bez edytora w panelu.
- Rate limit jest w pamięci procesu (nie współdzielony między instancjami).
- Retencja: automatycznie usuwane są tylko pliki eksportów po TTL; pliki źródłowe i szablony zostają do ręcznego usunięcia.
- Zarządzanie członkami: dodanie istniejącego konta po e-mailu; brak zaproszeń.
