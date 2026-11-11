"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md" };

export function Button({ variant = "primary", size = "md", className, ...rest }: BtnProps) {
  const styles = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-700 disabled:bg-indigo-300",
    secondary: "bg-white text-slate-800 border border-slate-300 hover:bg-slate-50 disabled:text-slate-400",
    ghost: "text-slate-700 hover:bg-slate-100 disabled:text-slate-400",
    danger: "bg-white text-red-700 border border-red-300 hover:bg-red-50 disabled:text-red-300",
  }[variant];
  return (
    <button
      type="button"
      className={cx("inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors disabled:cursor-not-allowed",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm", styles, className)}
      {...rest}
    />
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-slate-200 bg-white", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONES = {
  gray: "bg-slate-100 text-slate-700",
  green: "bg-emerald-100 text-emerald-800",
  amber: "bg-amber-100 text-amber-800",
  red: "bg-red-100 text-red-800",
  blue: "bg-indigo-100 text-indigo-800",
};

export function Badge({ tone = "gray", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={cx("inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone])}>{children}</span>;
}

const STATUS: Record<string, [string, keyof typeof TONES]> = {
  queued: ["W kolejce", "gray"], parsing: ["Odczyt pliku", "blue"], needs_mapping: ["Wymaga mapowania", "amber"],
  validating: ["Walidacja", "blue"], needs_review: ["Wymaga decyzji", "amber"], ready: ["Gotowy", "green"],
  exporting: ["Eksport", "blue"], completed: ["Zakończony", "green"], failed: ["Błąd", "red"],
  expired: ["Wygasł", "gray"], blocked: ["Błąd", "red"], review: ["Do decyzji", "amber"], skipped: ["Pominięty", "gray"],
};

export function StatusBadge({ status }: { status: string }) {
  const [label, tone] = STATUS[status] || [status, "gray"];
  return <Badge tone={tone}>{label}</Badge>;
}

export const SEVERITY: Record<string, [string, keyof typeof TONES]> = {
  BLOCKER: ["Blokuje", "red"], REVIEW: ["Do decyzji", "amber"], WARNING: ["Ostrzeżenie", "gray"],
};

export function SeverityBadge({ severity }: { severity: string }) {
  const [label, tone] = SEVERITY[severity] || [severity, "gray"];
  return <Badge tone={tone}>{label}</Badge>;
}

export function Alert({ tone = "red", children }: { tone?: "red" | "amber" | "blue" | "green"; children: ReactNode }) {
  const styles = {
    red: "border-red-200 bg-red-50 text-red-800", amber: "border-amber-200 bg-amber-50 text-amber-900",
    blue: "border-indigo-200 bg-indigo-50 text-indigo-900", green: "border-emerald-200 bg-emerald-50 text-emerald-900",
  }[tone];
  return <div role={tone === "red" ? "alert" : "status"} className={cx("rounded-md border px-3 py-2 text-sm", styles)}>{children}</div>;
}

export function ErrorNote({ error }: { error: string | null | undefined }) {
  return error ? <Alert>{error}</Alert> : null;
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(!/(^|\s)w-/.test(className || "") && "w-full", "rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm placeholder:text-slate-400", className)} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cx(!/(^|\s)w-/.test(className || "") && "w-full", "rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm", className)} {...rest}>{children}</select>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function Spinner({ label = "Wczytywanie…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600" />
      {label}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-500">{children}</p>;
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
            {head.map((h, i) => <th key={i} className="px-2 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}

export function Td({ children, className, title }: { children?: ReactNode; className?: string; title?: string }) {
  return <td title={title} className={cx("px-2 py-1.5 align-top", className)}>{children}</td>;
}

export function PageTitle({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-600">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  );
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" });
}

export function plural(n: number, one: string, few: string, many: string): string {
  if (n === 1) return `${n} ${one}`;
  const d = n % 10, h = n % 100;
  return `${n} ${d >= 2 && d <= 4 && !(h >= 12 && h <= 14) ? few : many}`;
}

export const MARKET_NAMES: Record<string, string> = { empik: "Empik", media_expert: "Media Expert", castorama: "Castorama" };
