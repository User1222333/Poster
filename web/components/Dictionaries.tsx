"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Alert, Badge, Button, Card, Empty, ErrorNote, Input, plural, Select, Spinner, Table, Td } from "@/lib/ui";

function Candidates({ items, onPick }: { items: Rec[]; onPick: (v: string) => void }) {
  if (!items?.length) return <span className="text-xs text-slate-400">brak podobnych</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((c) => (
        <button key={c.value} className="rounded border border-slate-300 px-1.5 py-0.5 text-xs hover:bg-slate-50" onClick={() => onPick(c.value)}
          title="Propozycja na podstawie podobieństwa tekstu — wymaga Twojej decyzji">
          {c.label || c.value}{c.score != null && <span className="text-slate-400"> · {Math.round(c.score * 100)}%</span>}
        </button>
      ))}
    </span>
  );
}

/** Przypisanie kategorii źródłowych do kategorii rynku — wyłącznie po zatwierdzeniu przez użytkownika. */
export function CategoryMapper({ projectId, onChanged }: { projectId: string; onChanged?: () => void }) {
  const data = useApi<Rec>(`/projects/${projectId}/categories`);
  const project = useApi<Rec>(`/projects/${projectId}`);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const { busy, error, run } = useAction();
  if (data.loading || project.loading) return <Spinner />;
  if (!data.data || !project.data) return <ErrorNote error={data.error || project.error} />;
  const d = data.data;
  if (!d.category_column) {
    return <Card title="Kategorie"><Empty>Szablon nie ma wskazanej kolumny kategorii — mapowanie kategorii nie jest potrzebne.</Empty></Card>;
  }
  const options: Rec[] = d.options;
  const pending = d.categories.filter((c: Rec) => c.source && !c.mapped_code).length;

  async function approve(source: string, code: string) {
    const res = await run(() => api("/category-mappings", {
      method: "PUT", json: { marketplace_id: d.marketplace_id, source_category: source, target_code: code, template_id: project.data!.template.id },
    }));
    if (res) { await data.reload(); onChanged?.(); }
  }
  async function remove(id: string) {
    const res = await run(() => api(`/category-mappings/${id}`, { method: "DELETE" }));
    if (res) { await data.reload(); onChanged?.(); }
  }

  return (
    <Card title="Kategorie" actions={pending ? <Badge tone="amber">Do przypisania: {pending}</Badge> : <Badge tone="green">Wszystkie przypisane</Badge>}>
      <p className="mb-3 text-sm text-slate-600">
        Kategoria rynku jest przypisywana automatycznie tylko dla zatwierdzonego poniżej aliasu. Podobieństwo nazw służy wyłącznie jako podpowiedź.
        Przypisuj do najniższego właściwego poziomu. Zatwierdzone aliasy są zapamiętywane dla tego rynku.
      </p>
      {options.length === 0 && <div className="mb-3"><Alert tone="amber">Szablon nie zawiera listy kategorii — wpisz kod kategorii ręcznie, dokładnie tak, jak wymaga go rynek.</Alert></div>}
      <Table head={["Kategoria w źródle", "Produkty", "Kategoria rynku", "Podpowiedzi", ""]}>
        {d.categories.map((c: Rec) => (
          <tr key={c.source} data-testid={`cat-${c.source}`}>
            <Td className="font-medium">{c.source || <span className="text-red-700">(brak kategorii w źródle)</span>}</Td>
            <Td>{c.products}</Td>
            <Td>
              {c.mapped_code ? (
                <span className="flex items-center gap-2"><Badge tone={c.mapped_valid ? "green" : "red"}>{c.mapped_code}</Badge>
                  {!c.mapped_valid && <span className="text-xs text-red-700">nie ma na liście szablonu</span>}</span>
              ) : !c.source ? <span className="text-xs text-slate-500">uzupełnij w źródle lub popraw w Centrum błędów</span>
                : options.length ? (
                  <Select className="w-64" aria-label={`Kategoria rynku dla ${c.source}`} value={choice[c.source] || ""} onChange={(e) => setChoice({ ...choice, [c.source]: e.target.value })}>
                    <option value="">— wybierz —</option>
                    {options.map((o) => <option key={o.code} value={o.code}>{o.label ? `${o.label} (${o.code})` : o.code}</option>)}
                  </Select>
                ) : <Input className="w-64" placeholder="kod kategorii" value={choice[c.source] || ""} onChange={(e) => setChoice({ ...choice, [c.source]: e.target.value })} />}
            </Td>
            <Td>{!c.mapped_code && c.source && <Candidates items={c.candidates} onPick={(v) => setChoice({ ...choice, [c.source]: v })} />}</Td>
            <Td>
              {c.mapped_code
                ? <Button size="sm" variant="ghost" disabled={busy} onClick={() => void remove(c.mapping_id)}>Usuń przypisanie</Button>
                : c.source && <Button size="sm" disabled={busy || !choice[c.source]} onClick={() => void approve(c.source, choice[c.source])}>Zatwierdź</Button>}
            </Td>
          </tr>
        ))}
      </Table>
      <div className="mt-3"><ErrorNote error={error} /></div>
    </Card>
  );
}

/** Wartości spoza listy dozwolonych — użytkownik wskazuje odpowiednik z listy rynku. */
export function ValueDictionary({ projectId, version, onChanged }: { projectId: string; version?: string; onChanged?: () => void }) {
  const data = useApi<Rec>(`/projects/${projectId}/unmapped-values?v=${version || ""}`);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const { busy, error, run } = useAction();
  if (data.loading) return <Spinner />;
  if (!data.data) return <ErrorNote error={data.error} />;
  const d = data.data;
  if (d.values.length === 0) return null;

  async function approve(v: Rec, target: string) {
    const res = await run(() => api("/value-mappings", {
      method: "PUT", json: { marketplace_id: d.marketplace_id, target_column: v.target_column, source_value: v.value, target_value: target, template_id: d.template_id },
    }));
    if (res) { await data.reload(); onChanged?.(); }
  }

  return (
    <Card title="Słownik wartości — wartości spoza listy rynku" actions={<Badge tone="amber">{d.values.length} do decyzji</Badge>}>
      <p className="mb-3 text-sm text-slate-600">
        Wskaż odpowiednik z listy dozwolonych wartości szablonu. Wpis zostanie zapamiętany dla tego rynku i zastosowany do wszystkich produktów po ponownej walidacji.
      </p>
      <Table head={["Pole rynku", "Wartość w danych", "Produkty", "Wartość z listy rynku", "Podpowiedzi", ""]}>
        {d.values.map((v: Rec) => {
          const key = `${v.target_column}|${v.value}`;
          return (
            <tr key={key} data-testid={`val-${v.value}`}>
              <Td className="text-xs">{v.label}</Td>
              <Td className="font-mono text-xs">{v.value}</Td>
              <Td>{plural(v.products, "produkt", "produkty", "produktów")}</Td>
              <Td>
                <Select className="w-48" aria-label={`Odpowiednik dla ${v.value}`} value={choice[key] || ""} onChange={(e) => setChoice({ ...choice, [key]: e.target.value })}>
                  <option value="">— wybierz —</option>
                  {v.options.map((o: Rec) => <option key={o.code} value={o.code}>{o.label ? `${o.label} (${o.code})` : o.code}</option>)}
                </Select>
              </Td>
              <Td><Candidates items={v.suggestions} onPick={(x) => setChoice({ ...choice, [key]: x })} /></Td>
              <Td><Button size="sm" disabled={busy || !choice[key]} onClick={() => void approve(v, choice[key])}>Zatwierdź</Button></Td>
            </tr>
          );
        })}
      </Table>
      <div className="mt-3"><ErrorNote error={error} /></div>
    </Card>
  );
}
