"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Alert, Badge, Button, Card, cx, Empty, ErrorNote, fmtDate, Input, plural, Select, SeverityBadge, Spinner, StatusBadge, Table, Td } from "@/lib/ui";
import { ValueDictionary } from "./Dictionaries";

const BUSY = ["queued", "validating", "exporting"];

function Counter({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
      <div className={cx("text-xl font-semibold", tone)}>{value ?? 0}</div>
      <div className="text-xs text-slate-600">{label}</div>
    </div>
  );
}

function RowDetail({ projectId, rowNumber, column, version, onChanged, onClose }: { projectId: string; rowNumber: number; column: string | null; version: number; onChanged: () => void; onClose: () => void }) {
  const detail = useApi<Rec>(`/projects/${projectId}/rows/${rowNumber}?v=${version}`);
  const project = useApi<Rec>(`/projects/${projectId}`);
  const [col, setCol] = useState<string | null>(column);
  const [value, setValue] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  useEffect(() => { setCol(column); setValue(null); }, [rowNumber, column]);
  if (detail.loading || project.loading) return <Card title="Szczegóły produktu"><Spinner /></Card>;
  if (!detail.data || !project.data) return <Card title="Szczegóły produktu"><ErrorNote error={detail.error || project.error} /></Card>;
  const d = detail.data;
  const columns: Rec[] = project.data.template.columns;
  const active = col || d.issues.find((i: Rec) => i.target_column)?.target_column || columns[0]?.key;
  const prov = d.row.provenance[active] || {};
  const current = d.row.values[active] ?? "";
  const req = project.data.template.requirements[active] || {};
  const draft = value ?? current;

  async function save() {
    const res = await run(() => api(`/projects/${projectId}/overrides`, { json: { row_number: rowNumber, target_column: active, value: draft } }));
    if (res) { setValue(null); onChanged(); }
  }
  async function revert(id: string) {
    const res = await run(() => api(`/projects/${projectId}/overrides/${id}/revert`, { method: "POST" }));
    if (res) onChanged();
  }

  return (
    <Card title={`Szczegóły produktu — wiersz ${rowNumber}${d.row.sku ? ` · ${d.row.sku}` : ""}`} actions={<Button size="sm" variant="ghost" onClick={onClose}>Zamknij</Button>}>
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Dane źródłowe (surowe)</h3>
          <div className="max-h-72 overflow-auto rounded-md border border-slate-200">
            <table className="w-full text-xs"><tbody>
              {Object.entries(d.raw as Record<string, string>).map(([k, v]) => (
                <tr key={k} className="border-b border-slate-100"><td className="w-36 bg-slate-50 px-2 py-1 font-medium">{k}</td><td className="break-all px-2 py-1 font-mono">{v || <span className="text-slate-400">(puste)</span>}</td></tr>
              ))}
            </tbody></table>
          </div>
        </div>
        <div className="space-y-3">
          <div>
            <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Zgłoszenia dla produktu</h3>
            {d.issues.length === 0 ? <p className="text-sm text-emerald-700">Brak zgłoszeń — wiersz gotowy.</p> : (
              <ul className="space-y-1 text-sm">{d.issues.map((i: Rec) => (
                <li key={i.id} className="flex items-start gap-2"><SeverityBadge severity={i.severity} />
                  <button className="text-left hover:underline" onClick={() => { if (i.target_column) { setCol(i.target_column); setValue(null); } }}>{i.message}</button></li>
              ))}</ul>
            )}
          </div>
          <div className="rounded-md border border-slate-200 p-3">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold uppercase text-slate-500">Pole rynku</span>
              <Select className="w-64" value={active} onChange={(e) => { setCol(e.target.value); setValue(null); }} aria-label="Pole rynku">
                {columns.map((c) => <option key={c.key} value={c.key}>{c.label ? `${c.label} (${c.code})` : c.code}</option>)}
              </Select>
            </div>
            <dl className="mb-2 space-y-0.5 text-xs text-slate-600">
              <div>Źródło: <span className="font-mono">{prov.source_field || "—"}</span></div>
              <div>Transformacja: {prov.transformation || "—"}</div>
              <div>Wartość przetworzona: <span className="font-mono text-slate-900">{current || "(pusta)"}</span>{prov.user_override && <> <Badge tone="blue">korekta ręczna</Badge></>}</div>
            </dl>
            {req.enum ? (
              <Select value={draft} onChange={(e) => setValue(e.target.value)} aria-label="Nowa wartość" data-testid="detail-value">
                <option value="">(pusta)</option>
                {!req.enum.some((o: Rec) => o.code === draft) && draft && <option value={draft}>{draft} — spoza listy</option>}
                {req.enum.map((o: Rec) => <option key={o.code} value={o.code}>{o.label ? `${o.label} (${o.code})` : o.code}</option>)}
              </Select>
            ) : <Input value={draft} onChange={(e) => setValue(e.target.value)} aria-label="Nowa wartość" data-testid="detail-value" />}
            <div className="mt-2 flex items-center gap-2">
              <Button size="sm" disabled={busy || draft === current} onClick={() => void save()} data-testid="detail-save">Zapisz korektę i zwaliduj ponownie</Button>
            </div>
            <p className="mt-1 text-xs text-slate-500">Wpisz wartość z rzeczywistych danych produktu. System nie generuje brakujących wartości.</p>
          </div>
          <ErrorNote error={error} />
          {d.history.length > 0 && (
            <div>
              <h3 className="mb-1 text-xs font-semibold uppercase text-slate-500">Historia korekt</h3>
              <ul className="space-y-1 text-xs">{d.history.map((h: Rec) => (
                <li key={h.id} className="flex flex-wrap items-center gap-2">
                  <span className="text-slate-500">{fmtDate(h.created_at)}</span>
                  <span className="font-mono">{h.target_column}: „{h.previous_value}” → „{h.value}”</span>
                  {h.batch_id && <Badge>masowa</Badge>}
                  {h.reverted_at ? <Badge>cofnięta</Badge> : <button className="text-indigo-700 hover:underline" disabled={busy} onClick={() => void revert(h.id)}>Przywróć poprzednią</button>}
                </li>
              ))}</ul>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

export function IssuesCenter({ projectId, onChanged }: { projectId: string; onChanged?: () => void }) {
  const project = useApi<Rec>(`/projects/${projectId}`, (d) => BUSY.includes(d.status));
  const [version, setVersion] = useState(0);
  const wasBusy = useRef(false);
  const status = project.data?.status;
  useEffect(() => {
    if (!status) return;
    const busyNow = BUSY.includes(status);
    if (wasBusy.current && !busyNow) { setVersion((v) => v + 1); onChanged?.(); }
    wasBusy.current = busyNow;
  }, [status, onChanged]);

  const [filter, setFilter] = useState<{ severity: string; code: string; column: string; category: string | null }>({ severity: "", code: "", column: "", category: null });
  const [selected, setSelected] = useState<{ row: number; column: string | null } | null>(null);
  const [bulkValue, setBulkValue] = useState("");
  const [bulk, setBulk] = useState<Rec | null>(null);
  const hasRun = !!project.data && project.data.last_run_version != null;
  const summary = useApi<Rec>(hasRun ? `/projects/${projectId}/issues/summary?v=${version}` : null);
  const qs = new URLSearchParams({ limit: "200", v: String(version) });
  if (filter.severity) qs.set("severity", filter.severity);
  if (filter.code) qs.set("code", filter.code);
  if (filter.column) qs.set("column", filter.column);
  if (filter.category !== null) qs.set("category", filter.category);
  const issues = useApi<Rec>(hasRun ? `/projects/${projectId}/issues?${qs}` : null);
  const { busy, error, run } = useAction();

  useEffect(() => setBulk(null), [filter, version]);

  if (project.loading) return <Spinner />;
  if (!project.data) return <ErrorNote error={project.error} />;
  const p = project.data;
  const c = p.counters || {};
  const isBusy = BUSY.includes(p.status);
  const labels: Record<string, string> = Object.fromEntries(p.template.columns.map((x: Rec) => [x.key, x.label || x.code]));
  const refresh = () => void project.reload();

  async function start() {
    const res = await run(() => api(`/projects/${projectId}/run`, { method: "POST" }));
    if (res) await project.reload();
  }
  async function bulkPreview() {
    const res = await run(() => api<Rec>(`/projects/${projectId}/overrides/bulk`, { json: { target_column: filter.column, issue_code: filter.code, value: bulkValue } }));
    if (res) setBulk(res);
  }
  async function bulkApply() {
    const res = await run(() => api<Rec>(`/projects/${projectId}/overrides/bulk`, { json: { target_column: filter.column, issue_code: filter.code, value: bulkValue, confirm: true, expected_count: bulk!.count } }));
    if (res) { setBulk(null); setBulkValue(""); await project.reload(); }
  }

  const groups: Rec[] = summary.data?.groups || [];
  const canBulk = !!filter.code && !!filter.column;

  return (
    <div className="space-y-4">
      <Card title={<span className="flex items-center gap-2">Walidacja <StatusBadge status={p.status} /></span>}
        actions={<Button disabled={busy || isBusy || p.rules_version === 0} onClick={() => void start()} data-testid="run-validation">{hasRun ? "Zwaliduj ponownie" : "Uruchom walidację"}</Button>}>
        {isBusy && <Spinner label="Trwa przetwarzanie i walidacja…" />}
        {p.status === "failed" && <Alert>Przetwarzanie nie powiodło się: {p.error}</Alert>}
        {p.rules_version === 0 && <Alert tone="amber">Najpierw zapisz reguły mapowania w kroku „Kategorie i reguły”.</Alert>}
        {hasRun && p.stale && !isBusy && <div className="mb-3"><Alert tone="amber">Reguły, szablon lub dane zmieniły się od ostatniej walidacji. Wyniki poniżej mogą być nieaktualne — zwaliduj ponownie.</Alert></div>}
        {hasRun && (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6" data-testid="counters">
            <Counter label="Zaimportowano" value={c.imported} tone="text-slate-900" />
            <Counter label="Kategoria przypisana automatycznie" value={c.auto_assigned} tone="text-slate-900" />
            <Counter label="Gotowe" value={c.ready} tone="text-emerald-700" />
            <Counter label="Do decyzji" value={c.review} tone="text-amber-700" />
            <Counter label="Z błędami" value={c.blocked} tone="text-red-700" />
            <Counter label="Pominięte regułą" value={c.skipped} tone="text-slate-500" />
          </div>
        )}
        {(p.project_issues || []).length > 0 && (
          <div className="mt-3 space-y-2">{p.project_issues.map((i: Rec) => <Alert key={i.code} tone={i.severity === "BLOCKER" ? "red" : "amber"}>{i.message}</Alert>)}</div>
        )}
        <div className="mt-3"><ErrorNote error={error} /></div>
      </Card>

      {hasRun && <ValueDictionary projectId={projectId} version={String(version)} onChanged={() => void start()} />}

      {hasRun && (
        <Card title="Co wymaga uwagi">
          {summary.loading ? <Spinner /> : groups.length === 0 ? <Empty>Brak zgłoszeń. Wszystkie wiersze przeszły walidację.</Empty> : (
            <ul className="divide-y divide-slate-100" data-testid="issue-groups">
              {groups.map((g) => {
                const on = filter.code === g.code && filter.column === (g.target_column || "");
                return (
                  <li key={`${g.code}-${g.target_column}`}>
                    <button className={cx("flex w-full flex-wrap items-center gap-2 px-2 py-2 text-left text-sm hover:bg-slate-50", on && "bg-indigo-50")}
                      onClick={() => setFilter(on ? { ...filter, code: "", column: "" } : { ...filter, severity: "", code: g.code, column: g.target_column || "" })}>
                      <SeverityBadge severity={g.severity} />
                      <span className="flex-1">{g.products === 1 ? g.sample : groupTitle(g)} <strong>— {plural(g.products, "produkt", "produkty", "produktów")}</strong></span>
                      <span className="font-mono text-xs text-slate-400">{g.code}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      )}

      {hasRun && groups.length > 0 && (
        <Card title="Lista problemów">
          <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <Select aria-label="Filtr: waga" value={filter.severity} onChange={(e) => setFilter({ ...filter, severity: e.target.value })}>
              <option value="">Każda waga</option><option value="BLOCKER">Blokujące</option><option value="REVIEW">Do decyzji</option><option value="WARNING">Ostrzeżenia</option>
            </Select>
            <Select aria-label="Filtr: przyczyna" value={filter.code} onChange={(e) => setFilter({ ...filter, code: e.target.value })}>
              <option value="">Każda przyczyna</option>
              {[...new Set(groups.map((g) => g.code))].map((code) => <option key={code} value={code}>{code}</option>)}
            </Select>
            <Select aria-label="Filtr: atrybut" value={filter.column} onChange={(e) => setFilter({ ...filter, column: e.target.value })}>
              <option value="">Każdy atrybut</option>
              {[...new Set(groups.map((g) => g.target_column).filter(Boolean))].map((k) => <option key={k} value={k}>{labels[k] || k}</option>)}
            </Select>
            <Select aria-label="Filtr: kategoria" value={filter.category ?? "__all__"} onChange={(e) => setFilter({ ...filter, category: e.target.value === "__all__" ? null : e.target.value })}>
              <option value="__all__">Każda kategoria</option>
              {(summary.data?.categories || []).map((x: Rec) => <option key={x.category} value={x.category}>{x.category || "(bez kategorii)"} · {x.products}</option>)}
            </Select>
          </div>
          {canBulk && (
            <div className="mb-3 rounded-md border border-indigo-200 bg-indigo-50 p-3">
              <div className="mb-2 text-sm font-medium">Poprawka masowa: {labels[filter.column] || filter.column} dla wszystkich produktów z problemem {filter.code}</div>
              <div className="flex flex-wrap items-center gap-2">
                <Input className="w-72 bg-white" placeholder="nowa wartość (rzeczywista, nie domyślna)" value={bulkValue} onChange={(e) => { setBulkValue(e.target.value); setBulk(null); }} data-testid="bulk-value" />
                <Button variant="secondary" disabled={busy || !bulkValue} onClick={() => void bulkPreview()} data-testid="bulk-preview">Pokaż, co się zmieni</Button>
              </div>
              {bulk && (
                <div className="mt-3 space-y-2 text-sm">
                  <div>Zmiana obejmie <strong>{plural(bulk.count, "rekord", "rekordy", "rekordów")}</strong>{bulk.sample.length > 0 && <>: {bulk.sample.map((s: Rec) => `${s.sku || `wiersz ${s.row_number}`} („${s.current || ""}” → „${bulkValue}”)`).join("; ")}{bulk.count > bulk.sample.length ? "…" : ""}</>}</div>
                  <Button disabled={busy || bulk.count === 0} onClick={() => void bulkApply()} data-testid="bulk-apply">Potwierdzam — zmień {plural(bulk.count, "rekord", "rekordy", "rekordów")}</Button>
                </div>
              )}
            </div>
          )}
          {issues.loading ? <Spinner /> : !issues.data || issues.data.rows.length === 0 ? <Empty>Brak problemów dla wybranych filtrów.</Empty> : (
            <>
              <div className="max-h-[30rem] overflow-auto rounded-md border border-slate-200">
                <Table head={["Wiersz", "SKU", "EAN", "Kategoria", "Pole", "Waga", "Problem", "Wartość", ""]}>
                  {issues.data.rows.map((i: Rec) => (
                    <tr key={i.id} className={cx(selected?.row === i.row_number && "bg-indigo-50")}>
                      <Td>{i.row_number}</Td><Td className="font-mono text-xs">{i.sku}</Td><Td className="font-mono text-xs">{i.gtin}</Td>
                      <Td className="text-xs">{i.category}</Td><Td className="text-xs">{i.target_column ? labels[i.target_column] || i.target_column : "—"}</Td>
                      <Td><SeverityBadge severity={i.severity} /></Td><Td className="max-w-md whitespace-normal">{i.message}</Td>
                      <Td className="max-w-40 truncate font-mono text-xs" title={i.processed_value || i.raw_value}>{i.processed_value || i.raw_value}</Td>
                      <Td><Button size="sm" variant="secondary" onClick={() => setSelected({ row: i.row_number, column: i.target_column })}>Szczegóły</Button></Td>
                    </tr>
                  ))}
                </Table>
              </div>
              <p className="mt-2 text-xs text-slate-500">Pokazano {issues.data.rows.length} z {issues.data.total} zgłoszeń.</p>
            </>
          )}
        </Card>
      )}

      {selected && <RowDetail projectId={projectId} rowNumber={selected.row} column={selected.column} version={version} onChanged={refresh} onClose={() => setSelected(null)} />}
    </div>
  );
}

function groupTitle(g: Rec): string {
  // Komunikat próbki bywa specyficzny dla wiersza; dla grup pokazujemy formę ogólną.
  const generic: Record<string, string> = {
    REQUIRED_MISSING: g.sample, GTIN_INVALID: "Błędny EAN/GTIN", ENUM_NOT_ALLOWED: `Wartość spoza listy dozwolonych${g.label ? `: ${g.label}` : ""}`,
    CATEGORY_UNMAPPED: "Kategoria źródłowa bez zatwierdzonego przypisania", CATEGORY_MISSING: "Brak kategorii źródłowej",
    DUPLICATE_SKU: "Zduplikowane SKU", GTIN_SHARED: "Ten sam EAN pod różnymi SKU", BANNED_PHRASE: `Zakazany zwrot${g.label ? ` w polu ${g.label}` : ""}`,
    TOO_LONG: `Przekroczona długość${g.label ? `: ${g.label}` : ""}`, URL_INVALID: `Niepoprawny adres URL${g.label ? `: ${g.label}` : ""}`,
    TYPE_INVALID: `Nieprawidłowa liczba${g.label ? `: ${g.label}` : ""}`, UNIT_MISSING: `Brak jednostki${g.label ? `: ${g.label}` : ""}`,
    DIMENSION_UNPARSEABLE: "Nie można rozdzielić wymiarów", DIMENSION_ORDER_UNTRUSTED: "Niepotwierdzona kolejność wymiarów",
    REVIEW_REQUIRED: `Wymaga ręcznego potwierdzenia${g.label ? `: ${g.label}` : ""}`, HTML_NOT_ALLOWED: "Niedozwolone znaczniki HTML",
  };
  return (generic[g.code] || g.sample).replace(/\.$/, "");
}
