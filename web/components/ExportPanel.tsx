"use client";

import { useState } from "react";
import { api, downloadFile } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Alert, Badge, Button, Card, Empty, ErrorNote, Field, fmtDate, Select, Spinner, StatusBadge } from "@/lib/ui";

const BUSY = ["queued", "exporting"];

export function ExportList({ projectId }: { projectId?: string }) {
  const list = useApi<Rec[]>(projectId ? `/exports?project_id=${projectId}` : "/exports", (d) => d.some((e) => BUSY.includes(e.status)));
  const [done, setDone] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  if (list.loading) return <Spinner />;
  if (!list.data) return <ErrorNote error={list.error} />;
  if (list.data.length === 0) return <Empty>Nie wygenerowano jeszcze żadnego pliku.</Empty>;

  async function get(id: string, file: string) {
    const name = await run(() => downloadFile(`/exports/${id}/download?file=${file}`));
    if (name) setDone(`Pobrano: ${name}`);
  }

  return (
    <div className="space-y-3">
      <ErrorNote error={error} />
      {done && <Alert tone="green">{done}</Alert>}
      {list.data.map((e) => {
        const expired = e.status === "expired" || (e.expires_at && new Date(e.expires_at) < new Date());
        return (
          <div key={e.id} className="rounded-md border border-slate-200 p-3" data-testid="export-item">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={expired && e.status === "completed" ? "expired" : e.status} />
              <span className="font-mono text-sm">{e.filename || `eksport ${e.format.toUpperCase()}`}</span>
              <Badge>{e.scope === "ready_only" ? "tylko gotowe" : "pełny plik"}</Badge>
              {!projectId && <span className="text-xs text-slate-500">{e.project_name}</span>}
              <span className="text-xs text-slate-500">{fmtDate(e.created_at)}</span>
            </div>
            {BUSY.includes(e.status) && <div className="mt-2"><Spinner label="Generowanie pliku…" /></div>}
            {e.status === "failed" && <div className="mt-2"><Alert>{e.error}</Alert></div>}
            {e.status === "completed" && (
              <div className="mt-2 space-y-2 text-sm">
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
                  <span>Zaimportowano: {e.counters.imported}</span><span>Autom. przypisano: {e.counters.auto_assigned}</span>
                  <span>Gotowe: {e.counters.ready}</span><span>Do decyzji: {e.counters.review}</span>
                  <span>Z błędami: {e.counters.blocked}</span><span><strong>Wyeksportowano: {e.counters.exported}</strong></span>
                </div>
                {e.compatibility.declared_compatible ? (
                  <Alert tone="green">Plik zgodny ze strukturą dostarczonego szablonu. {e.compatibility.note}</Alert>
                ) : (
                  <Alert tone="amber">
                    Nie można zadeklarować pełnej zgodności:
                    <ul className="ml-5 list-disc">{e.compatibility.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
                  </Alert>
                )}
                {expired ? <p className="text-xs text-slate-500">Czas życia pliku minął — wygeneruj eksport ponownie.</p> : (
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" disabled={busy} onClick={() => void get(e.id, "data")} data-testid="download-data">Pobierz plik</Button>
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => void get(e.id, "report")} data-testid="download-report">Raport braków (XLSX)</Button>
                    <Button size="sm" variant="secondary" disabled={busy} onClick={() => void get(e.id, "report_csv")}>Raport braków (CSV)</Button>
                    <span className="text-xs text-slate-500">Dostępny do {fmtDate(e.expires_at)}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function ExportPanel({ projectId }: { projectId: string }) {
  const project = useApi<Rec>(`/projects/${projectId}`, (d) => ["queued", "validating", "exporting"].includes(d.status));
  const [format, setFormat] = useState<string | null>(null);
  const [scope, setScope] = useState("ready_only");
  const [ack, setAck] = useState(false);
  const [nonce, setNonce] = useState(0);
  const { busy, error, run } = useAction();
  if (project.loading) return <Spinner />;
  if (!project.data) return <ErrorNote error={project.error} />;
  const p = project.data;
  const c = p.counters || {};
  const problems = (c.review || 0) + (c.blocked || 0);
  const fmt = format || p.template.format;
  const can = !p.stale && ["ready", "needs_review", "completed"].includes(p.status);

  async function create() {
    const res = await run(() => api(`/projects/${projectId}/exports`, { json: { format: fmt, scope, acknowledge_problems: ack } }));
    if (res) { setNonce(nonce + 1); await project.reload(); }
  }

  return (
    <div className="space-y-4">
      <Card title="Pobierz plik">
        {!can && <Alert tone="amber">Najpierw uruchom walidację na aktualnych regułach (krok „Walidacja”).</Alert>}
        {can && (
          <div className="space-y-4">
            {p.template.is_demo_fixture && <Alert tone="amber">Szablon demonstracyjny — plik dostanie prefiks DEMO-NIE-WYSYLAC i nie jest przeznaczony do wysyłki na marketplace.</Alert>}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Format">
                <Select value={fmt} onChange={(e) => setFormat(e.target.value)} data-testid="export-format">
                  {p.template.format === "xlsx" && <option value="xlsx">XLSX — na oryginalnym szablonie (z arkuszami pomocniczymi)</option>}
                  <option value="csv">CSV UTF-8 — nagłówki z szablonu</option>
                </Select>
              </Field>
              <Field label="Zakres">
                <Select value={scope} onChange={(e) => { setScope(e.target.value); setAck(false); }} data-testid="export-scope">
                  <option value="ready_only">Tylko gotowe wiersze ({c.ready || 0})</option>
                  <option value="all">Pełny plik ({(c.imported || 0) - (c.skipped || 0)}) + raport</option>
                </Select>
              </Field>
            </div>
            {scope === "all" && problems > 0 && (
              <Alert tone="red">
                <strong>Uwaga:</strong> pełny plik zawiera {problems} wierszy z nierozwiązanymi problemami. Marketplace najpewniej je odrzuci.
                <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />Rozumiem i mimo to chcę wygenerować pełny plik.</label>
              </Alert>
            )}
            {scope === "ready_only" && problems > 0 && <Alert tone="blue">{problems} wierszy z problemami nie trafi do pliku. Znajdziesz je w raporcie braków.</Alert>}
            <Button disabled={busy || (scope === "all" && problems > 0 && !ack) || (scope === "ready_only" && !c.ready)} onClick={() => void create()} data-testid="export-create">Generuj plik</Button>
            <p className="text-xs text-slate-500">Nazwy kolumn, ich kolejność i wiersze nagłówkowe pochodzą 1:1 z szablonu. Zgodność struktury nie gwarantuje akceptacji przez marketplace — rynek może stosować dodatkową weryfikację katalogową.</p>
          </div>
        )}
        <div className="mt-3"><ErrorNote error={error} /></div>
      </Card>
      <Card title="Wygenerowane pliki"><ExportList key={nonce} projectId={projectId} /></Card>
    </div>
  );
}
