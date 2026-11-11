"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Meta, Rec, Rule, Step } from "@/lib/types";
import { Alert, Badge, Button, Card, ErrorNote, Input, Select, Spinner } from "@/lib/ui";

const STEP_LABELS: Record<string, string> = {
  direct_field: "Pole źródłowe", fixed_value: "Stała wartość (zatwierdzona)", alias_lookup: "Pierwsze niepuste z pól",
  value_dictionary: "Słownik wartości rynku", category_mapping: "Mapowanie kategorii", regex_extract: "Wyodrębnij fragment",
  normalize_units: "Liczba / przelicz jednostkę", split_dimension: "Rozdziel wymiary (np. 140 x 260 cm)",
  concatenate: "Połącz pola i tekst", conditional: "Warunek (jeżeli…)", sanitize_description: "Oczyść opis (zakazane zwroty)",
  transform_html: "Oczyść HTML", skip_row: "Pomiń wiersz, gdy…", flag_missing: "Zgłoś brak wartości",
  require_review: "Wymagaj ręcznego potwierdzenia",
};
const OP_LABELS: Record<string, string> = { equals: "równe", not_equals: "różne od", contains: "zawiera", empty: "jest puste", not_empty: "nie jest puste", in: "jedno z (a, b, c)" };
const SOURCE_STEPS = ["direct_field", "fixed_value", "alias_lookup", "concatenate", "conditional"];

type Ctx = { meta: Meta; fields: { id: string; label: string }[] };

function defaults(type: string, ctx: Ctx): Step {
  const first = ctx.fields[0]?.id || "sku";
  switch (type) {
    case "direct_field": return { type, field: first };
    case "fixed_value": return { type, value: "", approved: false };
    case "alias_lookup": return { type, fields: [first] };
    case "regex_extract": return { type, pattern: ctx.meta.patterns[0].id };
    case "normalize_units": return { type, to: "cm", default_unit: "" };
    case "split_dimension": return { type, index: 0, order_trusted: false, to: "cm", default_unit: "" };
    case "concatenate": return { type, parts: [{ field: first }], separator: " " };
    case "conditional": return { type, when: { field: first, op: "not_empty", value: "" }, then: [], otherwise: [] };
    case "sanitize_description": return { type, banned_phrases: [], use_profile: true };
    case "transform_html": return { type, mode: "allowlist" };
    case "skip_row": return { type, when: { field: first, op: "empty", value: "" } };
    case "flag_missing": return { type, severity: "BLOCKER", message: "" };
    case "require_review": return { type, reason: "" };
    default: return { type };
  }
}

function FieldSelect({ value, onChange, ctx, allowValue }: { value: string; onChange: (v: string) => void; ctx: Ctx; allowValue?: boolean }) {
  const known = ctx.fields.some((f) => f.id === value) || (allowValue && value === "$value");
  return (
    <Select className="w-52" value={value} onChange={(e) => onChange(e.target.value)} aria-label="Pole źródłowe">
      {allowValue && <option value="$value">(bieżąca wartość)</option>}
      {!known && <option value={value}>{value} (brak w tym imporcie)</option>}
      {ctx.fields.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
    </Select>
  );
}

function UnitSelect({ value, onChange, ctx, empty, label }: { value: string; onChange: (v: string) => void; ctx: Ctx; empty: string; label: string }) {
  return (
    <Select className="w-28" aria-label={label} value={value || ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">{empty}</option>
      {ctx.meta.units.map((u) => <option key={u}>{u}</option>)}
    </Select>
  );
}

function WhenEditor({ when, onChange, ctx }: { when: Rec; onChange: (w: Rec) => void; ctx: Ctx }) {
  return (
    <span className="flex flex-wrap items-center gap-1">
      <FieldSelect value={when.field || "$value"} onChange={(v) => onChange({ ...when, field: v })} ctx={ctx} allowValue />
      <Select className="w-36" value={when.op} onChange={(e) => onChange({ ...when, op: e.target.value })}>
        {ctx.meta.ops.map((o) => <option key={o} value={o}>{OP_LABELS[o] || o}</option>)}
      </Select>
      {!["empty", "not_empty"].includes(when.op) && (
        <Input className="w-40" placeholder="wartość" value={Array.isArray(when.value) ? when.value.join(", ") : when.value || ""}
          onChange={(e) => onChange({ ...when, value: when.op === "in" ? e.target.value.split(",").map((x) => x.trim()) : e.target.value })} />
      )}
    </span>
  );
}

function StepConfig({ step, onChange, ctx, depth }: { step: Step; onChange: (s: Step) => void; ctx: Ctx; depth: number }) {
  const set = (patch: Rec) => onChange({ ...step, ...patch });
  switch (step.type) {
    case "direct_field":
      return <FieldSelect value={step.field} onChange={(v) => set({ field: v })} ctx={ctx} />;
    case "fixed_value":
      return (
        <span className="flex flex-wrap items-center gap-2">
          <Input className="w-48" placeholder="wartość" value={step.value} onChange={(e) => set({ value: e.target.value, approved: false })} />
          <label className="flex items-center gap-1 text-xs">
            <input type="checkbox" checked={step.approved === true} onChange={(e) => set({ approved: e.target.checked })} />
            Zatwierdzam tę wartość dla wszystkich wierszy
          </label>
        </span>
      );
    case "alias_lookup":
      return (
        <span className="flex flex-wrap items-center gap-1">
          {(step.fields as string[]).map((f, i) => (
            <span key={i} className="flex items-center">
              <FieldSelect value={f} onChange={(v) => set({ fields: step.fields.map((x: string, n: number) => (n === i ? v : x)) })} ctx={ctx} />
              {step.fields.length > 1 && <button className="px-1 text-slate-400 hover:text-red-600" aria-label="Usuń pole" onClick={() => set({ fields: step.fields.filter((_: string, n: number) => n !== i) })}>×</button>}
            </span>
          ))}
          <Button size="sm" variant="ghost" onClick={() => set({ fields: [...step.fields, ctx.fields[0]?.id || "sku"] })}>+ pole</Button>
          <span className="text-xs text-slate-500">różne wartości ⇒ „do decyzji”</span>
        </span>
      );
    case "value_dictionary":
      return <span className="text-xs text-slate-500">Zamienia wartość na kod z listy szablonu wg zatwierdzonego słownika. Brak wpisu ⇒ „do decyzji”.</span>;
    case "category_mapping":
      return <span className="text-xs text-slate-500">Tylko zatwierdzone aliasy kategorii. Brak ⇒ „do decyzji” z kandydatami.</span>;
    case "regex_extract":
      return (
        <Select className="w-64" value={step.pattern} onChange={(e) => set({ pattern: e.target.value })}>
          {ctx.meta.patterns.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </Select>
      );
    case "normalize_units":
      return (
        <span className="flex flex-wrap items-center gap-1 text-xs">
          na <UnitSelect value={step.to} onChange={(v) => set({ to: v || null })} ctx={ctx} empty="(tylko liczba)" label="Jednostka docelowa" />
          gdy brak jednostki: <UnitSelect value={step.default_unit} onChange={(v) => set({ default_unit: v || null })} ctx={ctx} empty="zgłoś" label="Jednostka, gdy brak w danych" />
        </span>
      );
    case "split_dimension":
      return (
        <span className="flex flex-wrap items-center gap-1 text-xs">
          <Select className="w-32" value={String(step.index)} onChange={(e) => set({ index: Number(e.target.value) })}>
            <option value="0">1. wymiar</option><option value="1">2. wymiar</option><option value="2">3. wymiar</option>
          </Select>
          na <UnitSelect value={step.to} onChange={(v) => set({ to: v || null })} ctx={ctx} empty="(bez przeliczania)" label="Jednostka docelowa" />
          brak jednostki: <UnitSelect value={step.default_unit} onChange={(v) => set({ default_unit: v || null })} ctx={ctx} empty="zgłoś" label="Jednostka, gdy brak w danych" />
          <label className="flex items-center gap-1">
            <input type="checkbox" checked={step.order_trusted === true} onChange={(e) => set({ order_trusted: e.target.checked })} />
            Kolejność wymiarów w źródle jest stała i zaufana
          </label>
        </span>
      );
    case "concatenate":
      return (
        <span className="flex flex-wrap items-center gap-1">
          {(step.parts as Rec[]).map((p, i) => {
            const upd = (np: Rec) => set({ parts: step.parts.map((x: Rec, n: number) => (n === i ? np : x)) });
            return (
              <span key={i} className="flex items-center rounded border border-slate-200 p-0.5">
                {"field" in p
                  ? <FieldSelect value={p.field} onChange={(v) => upd({ field: v })} ctx={ctx} />
                  : <Input className="w-28" placeholder="tekst" value={p.text} onChange={(e) => upd({ text: e.target.value })} />}
                <button className="px-1 text-slate-400 hover:text-red-600" aria-label="Usuń element" onClick={() => set({ parts: step.parts.filter((_: Rec, n: number) => n !== i) })}>×</button>
              </span>
            );
          })}
          <Button size="sm" variant="ghost" onClick={() => set({ parts: [...step.parts, { field: ctx.fields[0]?.id || "sku" }] })}>+ pole</Button>
          <Button size="sm" variant="ghost" onClick={() => set({ parts: [...step.parts, { text: "" }] })}>+ tekst</Button>
          <Input className="w-20" aria-label="Separator" placeholder="separator" value={step.separator} onChange={(e) => set({ separator: e.target.value })} />
        </span>
      );
    case "conditional":
      return (
        <div className="w-full space-y-2">
          <WhenEditor when={step.when} onChange={(w) => set({ when: w })} ctx={ctx} />
          {depth < 2 ? (
            <div className="grid gap-2 lg:grid-cols-2">
              <div className="rounded border border-emerald-200 p-2"><div className="mb-1 text-xs font-medium text-emerald-800">wtedy</div>
                <StepList steps={step.then || []} onChange={(s) => set({ then: s })} ctx={ctx} depth={depth + 1} /></div>
              <div className="rounded border border-slate-200 p-2"><div className="mb-1 text-xs font-medium text-slate-600">w przeciwnym razie</div>
                <StepList steps={step.otherwise || []} onChange={(s) => set({ otherwise: s })} ctx={ctx} depth={depth + 1} /></div>
            </div>
          ) : <span className="text-xs text-slate-500">Maksymalna głębokość warunków.</span>}
        </div>
      );
    case "sanitize_description":
      return (
        <span className="flex flex-wrap items-center gap-2 text-xs">
          <Input className="w-56" placeholder="dodatkowe zwroty (po przecinku)" value={(step.banned_phrases || []).join(", ")}
            onChange={(e) => set({ banned_phrases: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />
          <label className="flex items-center gap-1"><input type="checkbox" checked={step.use_profile !== false} onChange={(e) => set({ use_profile: e.target.checked })} />+ zwroty z profilu szablonu</label>
        </span>
      );
    case "transform_html":
      return (
        <span className="flex flex-wrap items-center gap-2 text-xs">
          <Select className="w-64" value={step.mode || "allowlist"} onChange={(e) => set({ mode: e.target.value })}>
            <option value="allowlist">Zostaw tylko dozwolone znaczniki</option>
            <option value="strip">Usuń cały HTML (sam tekst)</option>
          </Select>
          {step.mode !== "strip" && (
            <Input className="w-56" placeholder="puste = lista z profilu szablonu" value={step.allowed_tags ? step.allowed_tags.join(", ") : ""}
              onChange={(e) => set({ allowed_tags: e.target.value.trim() ? e.target.value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean) : undefined })} />
          )}
        </span>
      );
    case "skip_row":
      return <WhenEditor when={step.when} onChange={(w) => set({ when: w })} ctx={ctx} />;
    case "flag_missing":
      return (
        <span className="flex flex-wrap items-center gap-1">
          <Select className="w-36" value={step.severity || "BLOCKER"} onChange={(e) => set({ severity: e.target.value })}>
            <option value="BLOCKER">blokuje</option><option value="REVIEW">do decyzji</option><option value="WARNING">ostrzeżenie</option>
          </Select>
          <Input className="w-64" placeholder="komunikat (opcjonalnie)" value={step.message || ""} onChange={(e) => set({ message: e.target.value })} />
        </span>
      );
    case "require_review":
      return <Input className="w-72" placeholder="powód (opcjonalnie)" value={step.reason || ""} onChange={(e) => set({ reason: e.target.value })} />;
    default:
      return null;
  }
}

function StepList({ steps, onChange, ctx, depth, rowLevel }: { steps: Step[]; onChange: (s: Step[]) => void; ctx: Ctx; depth: number; rowLevel?: boolean }) {
  const types = rowLevel ? ["skip_row"] : ctx.meta.step_types.filter((t) => t !== "skip_row");
  return (
    <div className="space-y-1.5">
      {steps.map((step, i) => (
        <div key={i} className="flex flex-wrap items-start gap-2 rounded-md bg-slate-50 p-1.5">
          <span className="mt-1.5 w-4 text-right text-xs text-slate-400">{i + 1}.</span>
          <Select className="w-56" aria-label="Typ kroku" value={step.type} onChange={(e) => onChange(steps.map((s, n) => (n === i ? defaults(e.target.value, ctx) : s)))}>
            {types.filter((t) => i > 0 || depth > 0 || rowLevel || SOURCE_STEPS.includes(t)).map((t) => <option key={t} value={t}>{STEP_LABELS[t] || t}</option>)}
          </Select>
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2"><StepConfig step={step} onChange={(s) => onChange(steps.map((x, n) => (n === i ? s : x)))} ctx={ctx} depth={depth} /></div>
          <button className="mt-1 px-1 text-slate-400 hover:text-red-600" aria-label="Usuń krok" onClick={() => onChange(steps.filter((_, n) => n !== i))}>×</button>
        </div>
      ))}
      <Button size="sm" variant="ghost" onClick={() => onChange([...steps, defaults(rowLevel ? "skip_row" : steps.length === 0 && depth === 0 ? "direct_field" : "value_dictionary", ctx)])}>
        + {steps.length === 0 ? (rowLevel ? "dodaj warunek pominięcia" : "ustaw źródło") : "dodaj transformację"}
      </Button>
    </div>
  );
}

export function RulesEditor({ projectId, onSaved }: { projectId: string; onSaved?: () => void }) {
  const project = useApi<Rec>(`/projects/${projectId}`);
  const meta = useApi<Meta>("/meta");
  const saved = useApi<{ version: number; rules: Rule[] }>(`/projects/${projectId}/rules`);
  const importId = project.data?.import.id;
  const imp = useApi<Rec>(importId ? `/imports/${importId}` : null);
  const marketplace = project.data?.marketplace_id;
  const presets = useApi<Rec[]>(marketplace ? `/presets?marketplace_id=${marketplace}` : null);
  const [rules, setRules] = useState<Record<string, Step[]>>({});
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<Rec[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [presetName, setPresetName] = useState("");
  const [onlyRequired, setOnlyRequired] = useState(false);
  const { busy, error, run } = useAction();

  useEffect(() => {
    if (saved.data) {
      setRules(Object.fromEntries(saved.data.rules.map((r) => [r.target_column, r.steps])));
      setDirty(false);
    }
  }, [saved.data]);

  const ctx = useMemo<Ctx | null>(() => {
    if (!meta.data || !imp.data) return null;
    const map: Record<string, string> = imp.data.column_map || {};
    const labels = Object.fromEntries(meta.data.canonical_fields.map((f) => [f.id, f.label]));
    const fields: { id: string; label: string }[] = [];
    const seen = new Set<string>();
    let images = 0;
    for (const col of imp.data.columns as string[]) {
      const f = map[col];
      if (!f) fields.push({ id: `custom:${col}`, label: `${col} (kolumna źródłowa)` });
      else if (f === "images") { fields.push({ id: `images[${images}]`, label: `Zdjęcie ${images + 1} (${col})` }); images += 1; }
      else if (!seen.has(f)) { seen.add(f); fields.push({ id: f, label: `${labels[f] || f} (${col})` }); }
    }
    return { meta: meta.data, fields };
  }, [meta.data, imp.data]);

  if (project.loading || meta.loading || saved.loading || imp.loading) return <Spinner />;
  if (!project.data || !ctx || !saved.data) return <ErrorNote error={project.error || meta.error || saved.error || imp.error || "Brak danych."} />;
  const tpl = project.data.template;
  const columns: Rec[] = tpl.columns;
  const ROW = ctx.meta.row_target;
  const toList = (): Rule[] => [...columns.map((c) => c.key), ROW].filter((k) => rules[k]?.length).map((k) => ({ target_column: k, steps: rules[k] }));
  const update = (key: string, steps: Step[]) => { setRules({ ...rules, [key]: steps }); setDirty(true); setPreview(null); };
  const missingRequired = columns.filter((c) => tpl.requirements[c.key]?.required === true && !rules[c.key]?.length);

  async function suggest() {
    const res = await run(() => api<{ rules: Rule[] }>(`/projects/${projectId}/rules/suggest`));
    if (!res) return;
    const next = { ...rules };
    let added = 0;
    for (const r of res.rules) if (!next[r.target_column]?.length) { next[r.target_column] = r.steps; added += 1; }
    setRules(next);
    setDirty(added > 0 || dirty);
    setNote(`Zaproponowano źródło dla ${added} kolumn na podstawie nazw. Przejrzyj propozycje i zapisz reguły.`);
  }

  async function loadPreset(id: string) {
    const res = await run(() => api<Rec>(`/projects/${projectId}/rules/from-preset/${id}`));
    if (!res) return;
    setRules(Object.fromEntries(res.rules.map((r: Rule) => [r.target_column, r.steps])));
    setDirty(true);
    setPreview(null);
    setNote(`Wczytano preset (${res.rules.length} reguł).` +
      (res.template_changed ? " Uwaga: preset powstał dla innej wersji szablonu — sprawdź mapowania przed zapisaniem." : "") +
      (res.dropped.length ? ` Pominięto kolumny, których nie ma w tym szablonie: ${res.dropped.join(", ")}.` : ""));
  }

  async function doPreview() {
    const res = await run(() => api<{ rows: Rec[] }>(`/projects/${projectId}/rules/preview`, { json: { rules: toList(), limit: 3 } }));
    if (res) setPreview(res.rows);
  }

  async function save() {
    const res = await run(() => api<{ version: number }>(`/projects/${projectId}/rules`, { method: "PUT", json: { rules: toList() } }));
    if (res) { setNote(`Zapisano reguły (wersja ${res.version}). Uruchom walidację w następnym kroku.`); await saved.reload(); onSaved?.(); }
  }

  async function savePreset() {
    const res = await run(() => api(`/presets`, { json: { name: presetName, project_id: projectId } }));
    if (res) { setNote(`Zapisano preset „${presetName}”. Będzie dostępny przy kolejnych projektach dla tego rynku.`); setPresetName(""); await presets.reload(); }
  }

  const shown = onlyRequired ? columns.filter((c) => tpl.requirements[c.key]?.required === true || rules[c.key]?.length) : columns;

  return (
    <Card title={<span className="flex items-center gap-2">Reguły mapowania <Badge>{saved.data.version > 0 ? `wersja ${saved.data.version}` : "niezapisane"}</Badge>{dirty && <Badge tone="amber">zmiany niezapisane</Badge>}</span>}
      actions={<>
        <Button size="sm" variant="secondary" disabled={busy} onClick={() => void suggest()} data-testid="rules-suggest">Zaproponuj wg nazw kolumn</Button>
        {!!presets.data?.length && (
          <Select className="w-52" value="" onChange={(e) => e.target.value && void loadPreset(e.target.value)} aria-label="Wczytaj preset">
            <option value="">Wczytaj preset…</option>
            {presets.data.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.rule_count})</option>)}
          </Select>
        )}
      </>}>
      <p className="mb-3 text-sm text-slate-600">
        Po prawej kolumny szablonu rynku, po lewej źródło wartości i kolejne transformacje. Wartości nigdy nie są wymyślane — brak danych kończy się zgłoszeniem.
      </p>
      {note && <div className="mb-3"><Alert tone="blue">{note}</Alert></div>}
      <label className="mb-2 flex items-center gap-2 text-xs text-slate-600">
        <input type="checkbox" checked={onlyRequired} onChange={(e) => setOnlyRequired(e.target.checked)} />Pokaż tylko wymagane i już zmapowane
      </label>
      <div className="divide-y divide-slate-200 rounded-md border border-slate-200">
        {shown.map((c) => {
          const req = tpl.requirements[c.key] || {};
          return (
            <div key={c.key} className="grid gap-2 p-3 lg:grid-cols-[1fr_16rem]" data-testid={`rule-${c.key}`}>
              <div className="min-w-0 lg:order-1">
                <StepList steps={rules[c.key] || []} onChange={(s) => update(c.key, s)} ctx={ctx} depth={0} />
                {preview && rules[c.key]?.length > 0 && (
                  <div className="mt-2 grid gap-1 sm:grid-cols-3" data-testid={`preview-${c.key}`}>
                    {preview.map((p) => (
                      <div key={p.row_number} className="truncate rounded bg-indigo-50 px-2 py-1 font-mono text-xs" title={p.values[c.key]}>
                        <span className="text-slate-400">{p.sku}: </span>{p.values[c.key] || <span className="text-red-600">(puste)</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="lg:order-2 lg:border-l lg:border-slate-100 lg:pl-3">
                <div className="font-mono text-xs font-semibold">→ {c.code}</div>
                {c.label && <div className="text-xs text-slate-600">{c.label}</div>}
                <div className="mt-1 flex flex-wrap gap-1">
                  {req.required === true && <Badge tone="red">wymagane</Badge>}
                  {req.required == null && <Badge>wymagalność nieustalona</Badge>}
                  {req.type && <Badge tone="blue">{req.type}</Badge>}
                  {req.enum && <Badge>{req.enum.length} wartości</Badge>}
                  {c.key === tpl.category_column && <Badge tone="blue">kategoria</Badge>}
                </div>
              </div>
            </div>
          );
        })}
        <div className="p-3">
          <div className="mb-1 text-xs font-semibold text-slate-700">Reguły całego wiersza (pomijanie produktów)</div>
          <StepList steps={rules[ROW] || []} onChange={(s) => update(ROW, s)} ctx={ctx} depth={0} rowLevel />
        </div>
      </div>
      {preview && preview.some((p) => p.issues.length > 0) && (
        <div className="mt-3"><Alert tone="amber">
          Podgląd — zgłoszenia dla 3 pierwszych rekordów:
          <ul className="ml-5 list-disc">{preview.flatMap((p) => p.issues.map((i: Rec, n: number) => <li key={`${p.row_number}-${n}`}>{p.sku}: {i.message}</li>))}</ul>
        </Alert></div>
      )}
      {missingRequired.length > 0 && (
        <div className="mt-3"><Alert tone="amber">Wymagane kolumny bez reguły: {missingRequired.map((c) => c.label || c.code).join(", ")}. Każdy produkt dostanie dla nich zgłoszenie blokujące.</Alert></div>
      )}
      <div className="mt-3"><ErrorNote error={error} /></div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="secondary" disabled={busy || toList().length === 0} onClick={() => void doPreview()} data-testid="rules-preview">Podgląd na 3 rekordach</Button>
        <Button disabled={busy || !dirty || toList().length === 0} onClick={() => void save()} data-testid="rules-save">Zapisz reguły</Button>
        <span className="mx-2 hidden h-6 w-px bg-slate-200 sm:block" />
        <Input className="w-56" placeholder="nazwa presetu" value={presetName} onChange={(e) => setPresetName(e.target.value)} data-testid="preset-name" />
        <Button variant="secondary" disabled={busy || dirty || !presetName.trim() || saved.data.version === 0} onClick={() => void savePreset()} data-testid="preset-save">Zapisz jako preset</Button>
      </div>
    </Card>
  );
}
