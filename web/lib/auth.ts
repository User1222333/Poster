"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Tryb logowania: "supabase" (produkcja) albo "local" (development bez Supabase).
export const AUTH_MODE = process.env.NEXT_PUBLIC_AUTH_MODE === "supabase" ? "supabase" : "local";
const KEY = "mdb.token";

let client: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (!client) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !anon) throw new Error("Brak NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY.");
    client = createClient(url, anon);
  }
  return client;
}

export async function getToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  if (AUTH_MODE === "supabase") {
    const { data } = await supabase().auth.getSession();
    return data.session?.access_token ?? null;
  }
  return window.sessionStorage.getItem(KEY);
}

export function setLocalToken(token: string): void {
  window.sessionStorage.setItem(KEY, token);
}

export async function signOut(): Promise<void> {
  if (typeof window === "undefined") return;
  if (AUTH_MODE === "supabase") await supabase().auth.signOut();
  else window.sessionStorage.removeItem(KEY);
}
