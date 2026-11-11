"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Alert, Button, Card, cx, ErrorNote, MARKET_NAMES, Spinner, StatusBadge } from "@/lib/ui";
import { CategoryMapper } from "./Dictionaries";
import { ExportPanel } from "./ExportPanel";
import { ColumnMapper, ImportPicker, ImportPreview } from "./ImportSource";
import { IssuesCenter } from "./IssuesCenter";
import { RulesEditor } from "./RulesEditor";
import { TemplateEditor, TemplateEmpty, TemplatePicker } from "./TemplateSetup";

const STEPS = ["Źródło", "Marketplace i szablon", "Rozpoznanie kolumn", "Kategorie i reguły", "Walidacja", "Pobierz plik"];
const DRAFT = "mdb.wizard.draft";

function readDraft(): { importId: string | null; templateId: string | null; step: number } {
  try {
    return { importId: null, templateId: null, step: 1, ...JSON.parse(window.sessionStorage.getItem(DRAFT) || "{}") };
  } catch {
    return { importId: null, templateId: null, step: 1 };
  }
}

export function Wizard({ projectId }: { projectId?: string }) {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [importId, setImportId] = useState<string | null>(null);
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const project = useApi<Rec>(projectId ? `/projects/${projectId}` : null, (d) => ["queued", "validating", "exporting"].includes(d.status));
  const imp = useApi<Rec>(importId ? `/imports/${importId}` : null, (d) => ["queued", "parsing"].includes(d.status));
  const tpl = useApi<Rec>(templateId ? `/templates/${templateId}` : null, (d) => ["queued", "parsing"].includes(d.status));
  const { busy, error, run } = useAction();
  const reloadProject = project.reload, reloadImport = imp.reload, reloadTemplate = tpl.reload;
  // Odświeża dane w tle (bez przeładowania kroku), np. po zatwierdzeniu szablonu lub zapisaniu reguł.
  const bump = useCallback(() => {
    void reloadProject();
    void reloadImport();
    void reloadTemplate();
  }, [reloadProject, reloadImport, reloadTemplate]);

  useEffect(() => {
    if (projectId) {
      const s = Number(window.sessionStorage.getItem(`mdb.wizard.step.${projectId}`) || 4);
      setStep(s >= 1 && s <= 6 ? s : 4);
    } else {
      const d = readDraft();
      setImportId(d.importId);
      setTemplateId(d.templateId);
      setStep(Math.min(d.step, 3));
    }
    setLoaded(true);
  }, [projectId]);

  useEffect(() => {
    if (project.data) { setImportId(project.data.import.id); setTemplateId(project.data.template.id); }
  }, [project.data]);

  useEffect(() => {
    if (!loaded) return;
    if (projectId) window.sessionStorage.setItem(`mdb.wizard.step.${projectId}`, String(step));
    else window.sessionStorage.setItem(DRAFT, JSON.stringify({ importId, templateId, step }));
  }, [loaded, projectId, step, importId, templateId]);

  if (!loaded || (projectId && project.loading && !project.data)) return <Spinner />;
  if (projectId && !project.data) return <ErrorNote error={project.error} />;

  const p = project.data;
  const importParsed = !!imp.data && imp.data.columns?.length > 0 && !["queued", "parsing", "failed"].includes(imp.data.status);
  const importReady = imp.data?.status === "ready";
  const templateParsed = !!tpl.data && ["needs_review", "ready"].includes(tpl.data.status);
  const done = [importParsed, templateParsed && !!tpl.data?.confirmed, importReady, !!p && p.rules_version > 0,
    !!p && !p.stale && ["ready", "completed"].includes(p.status), !!p && p.status === "completed"];
  const canOpen = (n: number) => n <= 3 || !!projectId || (importReady && templateParsed);

  async function go(n: number) {
    if (n >= 4 && !projectId) {
      if (!importReady || !templateParsed) return;
      const name = `${imp.data!.filename} → ${MARKET_NAMES[tpl.data!.marketplace_id] || tpl.data!.marketplace_id}`;
      const created = await run(() => api<Rec>("/projects", { json: { name, import_id: importId, template_id: templateId } }));
      if (!created) return;
      window.sessionStorage.setItem(`mdb.wizard.step.${created.id}`, String(n));
      window.sessionStorage.removeItem(DRAFT);
      router.replace(`/kreator/${created.id}`);
      return;
    }
    setStep(n);
    bump();
    window.scrollTo({ top: 0 });
  }

  const nextHint = step === 1 ? (!importParsed ? "Wgraj lub wybierz plik źródłowy i poczekaj na jego odczyt." : null)
    : step === 2 ? (!templateParsed ? "Wgraj lub wybierz szablon." : null)
    : step === 3 ? (!importReady ? "Zatwierdź mapowanie kolumn." : !templateParsed ? "Wróć do kroku 2 i wskaż szablon." : null)
    : step === 4 ? (p && p.rules_version === 0 ? "Zapisz reguły mapowania." : null) : null;
  const locked = !!projectId;

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_18rem]">
      <div className="min-w-0 space-y-4">
        <ol className="flex flex-wrap gap-1.5" aria-label="Kroki kreatora">
          {STEPS.map((label, i) => {
            const n = i + 1;
            return (
              <li key={label}>
                <button disabled={!canOpen(n) || busy} onClick={() => void go(n)} aria-current={step === n ? "step" : undefined} data-testid={`step-${n}`}
                  className={cx("flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50",
                    step === n ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50")}>
                  <span className={cx("flex h-5 w-5 items-center justify-center rounded-full text-xs font-semibold",
                    step === n ? "bg-white text-indigo-700" : done[i] ? "bg-emerald-600 text-white" : "bg-slate-200 text-slate-700")}>{done[i] && step !== n ? "✓" : n}</span>
                  {label}
                </button>
              </li>
            );
          })}
        </ol>
        <ErrorNote error={error} />

        {step === 1 && (
          <>
            {locked ? <Alert tone="blue">Projekt jest powiązany z plikiem „{p!.import.filename}”. Aby użyć innego pliku, utwórz nowy projekt.</Alert>
              : <ImportPicker value={importId} onChange={(id) => { setImportId(id); bump(); }} />}
            {importId && <ImportPreview key={importId} importId={importId} onChanged={bump} />}
          </>
        )}
        {step === 2 && (
          <>
            {locked ? <Alert tone="blue">Projekt jest powiązany z szablonem „{p!.template.name}” (wersja {p!.template.version}). Nowa wersja szablonu wymaga nowego projektu i ponownej kontroli mapowań.</Alert>
              : <TemplatePicker value={templateId} onChange={(id) => { setTemplateId(id); bump(); }} />}
            {templateId ? <TemplateEditor key={templateId} templateId={templateId} onChanged={bump} /> : <TemplateEmpty />}
          </>
        )}
        {step === 3 && (importId ? <ColumnMapper key={importId} importId={importId} onConfirmed={bump} /> : <Alert tone="amber">Najpierw wskaż plik źródłowy w kroku 1.</Alert>)}
        {step === 4 && projectId && (
          <>
            {!importReady && imp.data && <Alert tone="amber">Mapowanie kolumn pliku źródłowego nie jest zatwierdzone (krok 3).</Alert>}
            <CategoryMapper projectId={projectId} onChanged={bump} />
            <RulesEditor projectId={projectId} onSaved={bump} />
          </>
        )}
        {step === 5 && projectId && <IssuesCenter projectId={projectId} onChanged={bump} />}
        {step === 6 && projectId && <ExportPanel projectId={projectId} />}

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
          <Button variant="secondary" disabled={step === 1 || busy} onClick={() => void go(step - 1)}>← Wstecz</Button>
          {nextHint && <span className="text-sm text-amber-700">{nextHint}</span>}
          {step < 6 && <Button disabled={busy || !!nextHint || !canOpen(step + 1)} onClick={() => void go(step + 1)} data-testid="next-step">Dalej: {STEPS[step]} →</Button>}
        </div>
      </div>

      <aside className="space-y-4">
        <Card title="Gotowość">
          {p ? (
            <div data-testid="readiness">
              <div className="flex items-end gap-2">
                <span className="text-3xl font-semibold">{p.readiness_percent}%</span>
                <StatusBadge status={p.status} />
              </div>
              <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-valuenow={p.readiness_percent} aria-valuemin={0} aria-valuemax={100}>
                <div className={cx("h-full", p.readiness_percent === 100 ? "bg-emerald-500" : "bg-indigo-500")} style={{ width: `${p.readiness_percent}%` }} />
              </div>
              <p className="mt-2 text-xs text-slate-500">Odsetek wierszy gotowych do eksportu wg ostatniej walidacji.</p>
              {p.blockers.length > 0 ? (
                <>
                  <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Co blokuje zakończenie</h3>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-xs text-slate-700">{p.blockers.map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
                </>
              ) : <p className="mt-3 text-sm text-emerald-700">Nic nie blokuje eksportu.</p>}
            </div>
          ) : (
            <ul className="space-y-2 text-sm">
              {[["Plik źródłowy odczytany", importParsed], ["Szablon odczytany", templateParsed], ["Szablon zatwierdzony", !!tpl.data?.confirmed], ["Kolumny źródła zatwierdzone", importReady]].map(([label, ok]) => (
                <li key={String(label)} className="flex items-center gap-2">
                  <span className={cx("flex h-4 w-4 items-center justify-center rounded-full text-[10px] text-white", ok ? "bg-emerald-600" : "bg-slate-300")}>{ok ? "✓" : ""}</span>{label}
                </li>
              ))}
            </ul>
          )}
        </Card>
        {tpl.data?.is_demo_fixture && <Alert tone="amber">Pracujesz na fikcyjnym szablonie DEMO. Wynik nie nadaje się do wysyłki.</Alert>}
      </aside>
    </div>
  );
}
