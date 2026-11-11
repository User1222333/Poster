"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";

export type Loadable<T> = { data: T | null; error: string | null; loading: boolean; reload: () => Promise<void> };

/** Pobiera zasób z API. `pollWhile` włącza odpytywanie co 1,2 s, dopóki zwraca true. */
export function useApi<T>(path: string | null, pollWhile?: (data: T) => boolean): Loadable<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const pollRef = useRef(pollWhile);
  useEffect(() => {
    pollRef.current = pollWhile;
  });

  const reload = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Nieznany błąd.");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setData(null);
    setLoading(!!path);
    void reload();
  }, [path, reload]);

  useEffect(() => {
    if (!data || !pollRef.current || !pollRef.current(data)) return;
    const t = setTimeout(() => void reload(), 1200);
    return () => clearTimeout(t);
  }, [data, reload]);

  return { data, error, loading, reload };
}

/** Wykonuje akcję i zwraca komunikat błędu z backendu (bez „optymistycznych” sukcesów). */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nieznany błąd.");
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}
