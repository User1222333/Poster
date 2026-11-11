# Plan wdrożenia — Marketplace Data Bridge (MVP bez AI)

## Audyt repozytorium
Repozytorium było puste (brak commitów, kodu, Supabase, UI). Projekt powstał od zera.

## Architektura
```
web/ (Next.js App Router, Tailwind)  ──HTTP + JWT──►  worker/app/api.py (FastAPI)
                                                        │  zapytania jako rola `authenticated` (RLS)
                                                        ▼
                                           Postgres / Supabase  ◄── worker/app/worker.py (kolejka, rola serwisowa)
                                                        │
                                           Storage (prywatny bucket / katalog lokalny)
worker/app/engine/  — czysty silnik (bez bazy): parsery, normalizacja, reguły, walidacja, eksport
```

## Decyzje architektoniczne
1. **Backend jako jedyna brama do danych.** Panel nie czyta tabel bezpośrednio; każde żądanie przechodzi przez FastAPI, które weryfikuje JWT, ustala organizację i wykonuje SQL w transakcji `SET LOCAL ROLE authenticated` + `request.jwt.claims`. Izolacja działa więc dwa razy: filtr `organization_id` w zapytaniu oraz RLS w bazie.
2. **Pliki tylko przez backend.** Bucket jest prywatny i nie ma polityk dla `authenticated`; pobranie eksportu to strumień z API po sprawdzeniu członkostwa i czasu życia (zamiast podpisanych URL — prostsze i bez wycieku linku).
3. **Kolejka w Postgresie** (`processing_jobs`, `FOR UPDATE SKIP LOCKED`, retry z backoffem, klucz idempotencji). Bez Redis/Celery. Worker to osobny proces; `INLINE_WORKER=1` tylko dla dev.
4. **Tryb lokalny bez Supabase.** `AUTH_MODE=local` + `worker/dev/local_auth_shim.sql` (schemat `auth`, rola `authenticated`, `auth.uid()`) pozwala uruchomić i przetestować całość, łącznie z RLS, na czystym Postgresie. Produkcyjnie: Supabase Auth (JWKS) + Supabase Storage.
5. **Silnik bez wiedzy o rynku.** Nie ma w kodzie żadnych nazw kolumn, kategorii ani słowników Empik/Media Expert/Castorama. Wszystko pochodzi z wgranego szablonu i zatwierdzeń użytkownika.
6. **Wymagania działają dopiero po zatwierdzeniu.** Odczyt z szablonu to propozycja; niezatwierdzony profil = BLOCKER projektu.
7. **Reguły jako dane** (JSON z zamkniętej listy 15 typów kroków, walidowane przy zapisie; regex tylko z allowlisty). Każdy zapis reguł tworzy nową wersję.
8. **Bez ORM**, surowy SQL (psycopg 3). Bez shadcn CLI — kilka własnych komponentów w tym samym stylu.

## Odstępstwa od preferowanego stosu
- shadcn/ui: zastąpione lekkimi komponentami Tailwind (`web/lib/ui.tsx`).
- Zod: walidacja wejścia jest po stronie API (Pydantic); panel pokazuje komunikaty backendu.
- Podpisane URL Storage: zastąpione strumieniowaniem przez API (patrz decyzja 2).
- Polars: niepotrzebny przy limicie 5 tys. wierszy.

## Etapy
M1 fundament → M2 import → M3 szablon → M4 reguły → M5 walidacja i korekty → M6 eksport. Stan: `docs/PROGRESS.md`.
