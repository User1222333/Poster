"use client";

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Alert, Badge, Button, Card, Empty, ErrorNote, Field, fmtDate, Input, Select, Spinner, StatusBadge, Table, Td } from "@/lib/ui";

const BUSY = ["queued", "parsing"];
const DELIMS: Record<string, string> = { ";": "średnik ;", ",": "przecinek ,", "\t": "tabulator", "|": "kreska |" };

/** Wgranie nowego pliku lub wybór istniejącego importu. */
export function ImportPicker({ value, onChange }: { value: string | null; onChange: (id: string) => void }) {
  const list = useApi<Rec[]>("/imports");
  const profiles = useApi<Rec[]>("/profiles");
  const [profileId, setProfileId] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const { busy, error, run } = useAction();

  async function upload(file: File | undefined) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    if (profileId) form.append("profile_id", profileId);
    const imp = await run(() => api<Rec>("/imports", { form }));
    if (fileRef.current) fileRef.current.value = "";
    if (imp) { await list.reload(); onChange(imp.id); }
  }

  async function demo() {
    const imp = await run(() => api<Rec>("/imports/demo", { method: "POST" }));
    if (imp) { await list.reload(); onChange(imp.id); }
  }

  return (
    <Card title="Plik źródłowy">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <Field label="Wgraj katalog (CSV UTF-8 lub XLSX)" hint="Makra (XLSM) i stare pliki XLS nie są obsługiwane.">
            <input ref={fileRef} type="file" accept=".csv,.txt,.xlsx" disabled={busy} data-testid="import-file"
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-indigo-600 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white"
              onChange={(e) => void upload(e.target.files?.[0])} />
          </Field>
          {!!profiles.data?.length && (
            <Field label="Zastosuj zapisany profil importu" hint="Profil przywraca mapowanie kolumn z poprzednich importów.">
              <Select value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                <option value="">— bez profilu —</option>
                {profiles.data.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </Select>
            </Field>
          )}
          <Button variant="secondary" disabled={busy} onClick={() => void demo()}>Użyj danych demonstracyjnych (20 produktów)</Button>
          {busy && <Spinner label="Wysyłanie pliku…" />}
          <ErrorNote error={error} />
        </div>
        <Field label="…albo wybierz wcześniejszy import">
          <Select value={value || ""} onChange={(e) => e.target.value && onChange(e.target.value)}>
            <option value="">— wybierz —</option>
            {(list.data || []).map((i) => (
              <option key={i.id} value={i.id}>{i.filename} · {i.row_count} wierszy · {fmtDate(i.created_at)}</option>
            ))}
          </Select>
        </Field>
      </div>
    </Card>
  );
}

/** Podgląd odczytu pliku + opcje parsowania (arkusz, wiersz nagłówka, separator, kodowanie). */
export function ImportPreview({ importId, onChanged }: { importId: string; onChanged?: () => void }) {
  const imp = useApi<Rec>(`/imports/${importId}`, (d) => BUSY.includes(d.status));
  const status = imp.data?.status;
  const rows = useApi<Rec[]>(status && !BUSY.includes(status) && status !== "failed" ? `/imports/${importId}/rows?limit=50&s=${status}${imp.data?.row_count}` : null);
  const { busy, error, run } = useAction();

  if (imp.loading) return <Spinner />;
  if (imp.error || !imp.data) return <ErrorNote error={imp.error} />;
  const d = imp.data;
  const opts = d.parse_options || {};
  const det = d.detected || {};

  async function setOption(patch: Rec) {
    const res = await run(() => api(`/imports/${importId}/parse-options`, { method: "PUT", json: patch }));
    if (res) { await imp.reload(); onChanged?.(); }
  }

  return (
    <Card title={<span className="flex items-center gap-2">Odczyt pliku: {d.filename} <StatusBadge status={d.status} /></span>}>
      {BUSY.includes(d.status) && <Spinner label="Plik jest przetwarzany…" />}
      {d.status === "failed" && <Alert>Nie udało się odczytać pliku: {d.error}</Alert>}
      {det.encoding_required && (
        <div className="space-y-3">
          <Alert tone="amber">Plik nie jest zapisany w UTF-8. Wybierz kodowanie, w którym polskie znaki wyglądają poprawnie.</Alert>
          {Object.entries(det.encoding_candidates as Record<string, string>).map(([enc, sample]) => (
            <div key={enc} className="rounded-md border border-slate-200 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-sm font-medium">{enc}</span>
                <Button size="sm" disabled={busy} onClick={() => void setOption({ encoding: enc })}>Użyj tego kodowania</Button>
              </div>
              <pre className="max-h-24 overflow-auto whitespace-pre-wrap text-xs text-slate-700">{sample}</pre>
            </div>
          ))}
        </div>
      )}
      {!BUSY.includes(d.status) && d.status !== "failed" && !det.encoding_required && (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {det.sheets && (
              <Field label="Arkusz">
                <Select value={opts.sheet || ""} disabled={busy} onChange={(e) => void setOption({ sheet: e.target.value })}>
                  {det.sheets.map((s: Rec) => <option key={s.name} value={s.name}>{s.name} ({s.rows} wierszy)</option>)}
                </Select>
              </Field>
            )}
            {det.delimiters && (
              <Field label="Separator">
                <Select value={opts.delimiter || ""} disabled={busy} onChange={(e) => void setOption({ delimiter: e.target.value })}>
                  {Object.entries(DELIMS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </Select>
              </Field>
            )}
            <Field label="Wiersz nagłówka">
              <Input type="number" min={1} max={200} defaultValue={opts.header_row || 1} disabled={busy} key={opts.header_row}
                onBlur={(e) => Number(e.target.value) !== opts.header_row && void setOption({ header_row: Number(e.target.value) })} />
            </Field>
            <div className="text-sm text-slate-600">
              <div>Wierszy danych: <strong>{d.row_count}</strong></div>
              <div>Kolumn: <strong>{d.columns.length}</strong></div>
              {det.encoding && <div>Kodowanie: {det.encoding}</div>}
            </div>
          </div>
          <ErrorNote error={error} />
          {rows.data && rows.data.length > 0 ? (
            <div className="max-h-96 overflow-auto rounded-md border border-slate-200">
              <Table head={["#", ...d.columns]}>
                {rows.data.map((r) => (
                  <tr key={r.row_number}>
                    <Td className="text-xs text-slate-400">{r.row_number}</Td>
                    {d.columns.map((c: string) => (
                      <Td key={c} className="max-w-56 truncate font-mono text-xs" title={r.raw[c]}>{r.raw[c]}</Td>
                    ))}
                  </tr>
                ))}
              </Table>
            </div>
          ) : <Empty>Brak wierszy danych pod wskazanym nagłówkiem.</Empty>}
          <p className="text-xs text-slate-500">Podgląd pierwszych 50 wierszy. Identyfikatory (SKU, EAN) są przechowywane jako tekst — zera wiodące nie są gubione.</p>
        </div>
      )}
    </Card>
  );
}

/** Mapowanie kolumn źródła na pola kanoniczne + zapis profilu + problemy w danych źródłowych. */
export function ColumnMapper({ importId, onConfirmed }: { importId: string; onConfirmed?: () => void }) {
  const imp = useApi<Rec>(`/imports/${importId}`, (d) => BUSY.includes(d.status));
  const meta = useApi<Rec>("/meta");
  const ready = imp.data?.status === "ready";
  const issues = useApi<Rec>(ready ? `/imports/${importId}/issues` : null);
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const [profile, setProfile] = useState("");
  const { busy, error, run } = useAction();

  if (imp.loading || meta.loading) return <Spinner />;
  if (!imp.data || !meta.data) return <ErrorNote error={imp.error || meta.error} />;
  const d = imp.data;
  if (BUSY.includes(d.status)) return <Card title="Rozpoznanie kolumn"><Spinner label="Plik jest przetwarzany…" /></Card>;
  if (!d.columns.length) return <Card title="Rozpoznanie kolumn"><Empty>Najpierw dokończ odczyt pliku w kroku „Źródło”.</Empty></Card>;

  const map: Record<string, string> = draft ?? d.column_map;
  const conflicts: Record<string, string[]> = d.detected?.conflicts || {};
  const conflictCols = new Set(Object.values(conflicts).flat());
  const used = Object.values(map);
  const dup = meta.data.canonical_fields.filter((f: Rec) => f.id !== "images" && used.filter((u) => u === f.id).length > 1);

  async function save() {
    const res = await run(() => api(`/imports/${importId}/column-map`, {
      method: "PUT", json: { column_map: map, save_profile_name: profile.trim() || null },
    }));
    if (res) { setDraft(null); await imp.reload(); onConfirmed?.(); }
  }

  return (
    <div className="space-y-4">
      <Card title="Rozpoznanie kolumn" actions={d.column_map_confirmed && !draft ? <Badge tone="green">Zatwierdzone</Badge> : <Badge tone="amber">Do zatwierdzenia</Badge>}>
        <p className="mb-3 text-sm text-slate-600">
          Propozycje pochodzą ze słownika aliasów nazw kolumn. Kolumny bez przypisania pozostają dostępne jako pola niestandardowe.
        </p>
        {d.detected?.profile_applied && <div className="mb-3"><Alert tone="blue">Zastosowano profil „{d.detected.profile_applied}”.{d.detected.profile_missing_columns?.length ? ` Brakujące kolumny z profilu: ${d.detected.profile_missing_columns.join(", ")}.` : ""}</Alert></div>}
        {Object.keys(conflicts).length > 0 && (
          <div className="mb-3"><Alert tone="amber">
            Konflikt: {Object.entries(conflicts).map(([f, cols]) => `kolumny ${cols.map((c) => `„${c}”`).join(" i ")} pasują do tego samego pola (${f})`).join("; ")}. Wskaż właściwą ręcznie.
          </Alert></div>
        )}
        <Table head={["Kolumna w pliku", "Pole w systemie", ""]}>
          {d.columns.map((c: string) => (
            <tr key={c}>
              <Td className="font-medium">{c}</Td>
              <Td>
                <Select aria-label={`Pole dla kolumny ${c}`} value={map[c] || ""} onChange={(e) => {
                  const next = { ...map };
                  if (e.target.value) next[c] = e.target.value; else delete next[c];
                  setDraft(next);
                }}>
                  <option value="">— pole niestandardowe —</option>
                  {meta.data!.canonical_fields.map((f: Rec) => <option key={f.id} value={f.id}>{f.label}</option>)}
                </Select>
              </Td>
              <Td>{conflictCols.has(c) && !map[c] && <Badge tone="amber">wymaga decyzji</Badge>}</Td>
            </tr>
          ))}
        </Table>
        {dup.length > 0 && <div className="mt-3"><Alert>Pole przypisano do więcej niż jednej kolumny: {dup.map((f: Rec) => f.label).join(", ")}.</Alert></div>}
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <div className="w-72">
            <Field label="Zapisz jako profil importu (opcjonalnie)">
              <Input placeholder="np. Eksport Sellasist firmy" value={profile} onChange={(e) => setProfile(e.target.value)} />
            </Field>
          </div>
          <Button disabled={busy || dup.length > 0} onClick={() => void save()}>
            {d.column_map_confirmed && !draft ? "Zatwierdź ponownie" : "Zatwierdź mapowanie kolumn"}
          </Button>
        </div>
        <div className="mt-3"><ErrorNote error={error} /></div>
      </Card>
      {ready && issues.data && (
        <Card title="Problemy wykryte w danych źródłowych">
          {issues.data.summary.length === 0 ? <Empty>Nie wykryto problemów w danych źródłowych.</Empty> : (
            <>
              <div className="mb-3 flex flex-wrap gap-2">
                {issues.data.summary.map((s: Rec) => <Badge key={s.code} tone="amber">{SOURCE_CODES[s.code] || s.code}: {s.count}</Badge>)}
              </div>
              <div className="max-h-72 overflow-auto">
                <Table head={["Wiersz", "SKU", "EAN", "Problem", "Wartość"]}>
                  {issues.data.rows.flatMap((r: Rec) => r.source_issues.map((i: Rec, n: number) => (
                    <tr key={`${r.row_number}-${n}`}>
                      <Td>{r.row_number}</Td><Td className="font-mono text-xs">{r.sku}</Td><Td className="font-mono text-xs">{r.gtin}</Td>
                      <Td>{i.message}</Td><Td className="max-w-48 truncate font-mono text-xs">{i.value}</Td>
                    </tr>
                  )))}
                </Table>
              </div>
              <p className="mt-2 text-xs text-slate-500">System niczego nie poprawia sam — błędny EAN lub brakującą wartość trzeba poprawić u źródła albo ręcznie w Centrum błędów.</p>
            </>
          )}
        </Card>
      )}
    </div>
  );
}

const SOURCE_CODES: Record<string, string> = {
  SKU_MISSING: "Brak SKU", TITLE_MISSING: "Pusta nazwa", GTIN_MISSING: "Brak EAN", GTIN_INVALID: "Błędny EAN",
  IMAGES_MISSING: "Brak zdjęć", URL_INVALID: "Błędny URL", NUMBER_INVALID: "Błędna liczba",
  SKU_DUPLICATE: "Duplikat SKU", GTIN_SHARED: "EAN pod wieloma SKU",
};
