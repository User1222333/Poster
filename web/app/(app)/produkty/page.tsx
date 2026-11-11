"use client";

import { useState } from "react";
import { useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Badge, Button, Card, Empty, ErrorNote, Input, PageTitle, Select, Spinner, Table, Td } from "@/lib/ui";

const PAGE = 50;

export default function ProductsPage() {
  const imports = useApi<Rec[]>("/imports");
  const [importId, setImportId] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const qs = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (importId) qs.set("import_id", importId);
  if (query) qs.set("q", query);
  const list = useApi<Rec>(`/products?${qs}`);
  return (
    <>
      <PageTitle title="Moje produkty" subtitle="Produkty w modelu kanonicznym, zbudowane z zatwierdzonych importów. Puste pole oznacza brak danych w źródle — nic nie jest uzupełniane automatycznie." />
      <Card>
        <form className="mb-3 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setOffset(0); setQuery(q.trim()); }}>
          <Input className="w-64" placeholder="Szukaj: SKU, EAN lub nazwa" value={q} onChange={(e) => setQ(e.target.value)} />
          <Select className="w-72" value={importId} onChange={(e) => { setImportId(e.target.value); setOffset(0); }} aria-label="Import">
            <option value="">Wszystkie importy</option>
            {(imports.data || []).map((i) => <option key={i.id} value={i.id}>{i.filename}</option>)}
          </Select>
          <Button type="submit" variant="secondary">Szukaj</Button>
        </form>
        <ErrorNote error={list.error} />
        {list.loading ? <Spinner /> : !list.data?.rows.length ? <Empty>Brak produktów. Zatwierdź mapowanie kolumn w imporcie, aby zbudować produkty.</Empty> : (
          <>
            <Table head={["SKU", "EAN", "Nazwa", "Kategoria", "Uwagi do źródła", "Plik", ""]}>
              {list.data.rows.map((p: Rec) => (
                <tr key={p.id}>
                  <Td className="font-mono text-xs">{p.sku}</Td><Td className="font-mono text-xs">{p.gtin}</Td>
                  <Td className="max-w-72 truncate" title={p.title}>{p.title}</Td><Td className="text-xs">{p.category_path}</Td>
                  <Td>{p.source_issues.length > 0 ? <Badge tone="amber">{p.source_issues.length}</Badge> : <Badge tone="green">brak</Badge>}</Td>
                  <Td className="text-xs text-slate-500">{p.filename}</Td>
                  <Td><Button size="sm" variant="ghost" onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? "Zwiń" : "Pola"}</Button></Td>
                </tr>
              ))}
            </Table>
            {open !== null && (() => {
              const p = list.data!.rows.find((x: Rec) => x.id === open);
              if (!p) return null;
              const flat: [string, string][] = Object.entries(p.data).filter(([k]) => k !== "custom_attributes").map(([k, v]) => [k, Array.isArray(v) ? v.join(" | ") : String(v ?? "")]);
              const custom: [string, string][] = Object.entries(p.data.custom_attributes || {}).map(([k, v]) => [`niestandardowe: ${k}`, String(v)]);
              return (
                <div className="mt-3 rounded-md border border-slate-200 p-3">
                  <div className="mb-2 text-sm font-semibold">{p.title || p.sku}</div>
                  <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2">
                    {[...flat, ...custom].map(([k, v]) => <div key={k} className="flex gap-2"><dt className="w-40 shrink-0 text-slate-500">{k}</dt><dd className="break-all font-mono">{v || "—"}</dd></div>)}
                  </dl>
                  {p.source_issues.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-amber-800">{p.source_issues.map((i: Rec, n: number) => <li key={n}>{i.message}</li>)}</ul>}
                </div>
              );
            })()}
            <div className="mt-3 flex items-center gap-3 text-sm">
              <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Poprzednie</Button>
              <span className="text-slate-600">{offset + 1}–{Math.min(offset + PAGE, list.data.total)} z {list.data.total}</span>
              <Button size="sm" variant="secondary" disabled={offset + PAGE >= list.data.total} onClick={() => setOffset(offset + PAGE)}>Następne →</Button>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
