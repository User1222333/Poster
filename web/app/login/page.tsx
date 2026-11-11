"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { AUTH_MODE, setLocalToken, supabase } from "@/lib/auth";
import { useAction } from "@/lib/hooks";
import { Alert, Button, ErrorNote, Field, Input } from "@/lib/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [info, setInfo] = useState<string | null>(null);
  const { busy, error, run } = useAction();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setInfo(null);
    await run(async () => {
      if (AUTH_MODE === "supabase") {
        const auth = supabase().auth;
        const res = mode === "login"
          ? await auth.signInWithPassword({ email, password })
          : await auth.signUp({ email, password });
        if (res.error) throw new Error(res.error.message);
        if (!res.data.session) {
          setInfo("Konto utworzone. Potwierdź adres e-mail, a następnie zaloguj się.");
          return;
        }
      } else {
        const res = await api<{ access_token: string }>(`/auth/local/${mode}`, { json: { email, password }, auth: false });
        setLocalToken(res.access_token);
      }
      router.replace("/");
    });
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-slate-200 bg-white p-6">
        <div>
          <h1 className="text-lg font-semibold">Marketplace Data Bridge</h1>
          <p className="mt-1 text-sm text-slate-600">
            {mode === "login" ? "Zaloguj się do panelu." : "Utwórz konto i własną organizację."}
          </p>
        </div>
        {AUTH_MODE === "local" && (
          <Alert tone="amber">Tryb lokalny (development). W produkcji logowanie obsługuje Supabase Auth.</Alert>
        )}
        <Field label="E-mail">
          <Input type="email" name="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Hasło" hint="Co najmniej 8 znaków.">
          <Input type="password" name="password" required minLength={8} value={password}
            autoComplete={mode === "login" ? "current-password" : "new-password"} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <ErrorNote error={error} />
        {info && <Alert tone="green">{info}</Alert>}
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? "Proszę czekać…" : mode === "login" ? "Zaloguj się" : "Zarejestruj się"}
        </Button>
        <button type="button" className="w-full text-center text-sm text-indigo-700 hover:underline"
          onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "Nie masz konta? Zarejestruj się" : "Masz już konto? Zaloguj się"}
        </button>
      </form>
    </main>
  );
}
