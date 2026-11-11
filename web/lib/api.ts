"use client";

import { getToken, signOut } from "./auth";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: Record<string, unknown>;
  constructor(status: number, message: string, code?: string, details?: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let message = `Błąd serwera (${res.status}).`;
  let code: string | undefined;
  let details: Record<string, unknown> | undefined;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail)) {
      message = body.detail.map((d: { msg?: string; loc?: string[] }) =>
        `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    }
    code = body.code;
    details = body.details;
  } catch {
    /* odpowiedź bez JSON */
  }
  return new ApiError(res.status, message, code, details);
}

type Options = { method?: string; json?: unknown; form?: FormData; auth?: boolean; headers?: Record<string, string> };

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers || {}) };
  if (opts.auth !== false) {
    const token = await getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  let body: BodyInit | undefined;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  } else if (opts.form) {
    body = opts.form;
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { method: opts.method || (body ? "POST" : "GET"), headers, body, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Brak połączenia z serwerem przetwarzania. Sprawdź, czy backend działa.");
  }
  if (res.status === 401 && opts.auth !== false) {
    await signOut();
    // Twarde przekierowanie czyści stan klienta po wygaśnięciu sesji.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    if (typeof window !== "undefined") window.location.href = "/login";
  }
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

export async function downloadFile(path: string): Promise<string> {
  const token = await getToken();
  const res = await fetch(`${API_URL}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw await parseError(res);
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const name = match ? decodeURIComponent(match[1]) : "plik";
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
  return name;
}
