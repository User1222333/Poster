"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Button, Card, Empty, ErrorNote, fmtDate, MARKET_NAMES, PageTitle, Spinner, StatusBadge, Table, Td } from "@/lib/ui";

function Section({ title, path, head, row, empty }: { title: string; path: string; head: string[]; row: (r: Rec) => (string | number)[]; empty: string }) {
  const list = useApi<Rec[]>(path);
  const { busy, error, run } = useAction();
  async function remove(id: string) {
    const res = await run(() => api(`${path.split("?")[0]}/${id}`, { method: "DELETE" }));
    if (res) await list.reload();
  }
  return (
    <Card title={title}>
      <ErrorNote error={error || list.error} />
      {list.loading ? <Spinner /> : !list.data?.length ? <Empty>{empty}</Empty> : (
        <Table head={[...head, ""]}>
          {list.data.map((r) => (
            <tr key={r.id}>
              {row(r).map((v, i) => <Td key={i} className={i === 0 ? "font-medium" : "text-sm"}>{v}</Td>)}
              <Td><Button size="sm" variant="danger" disabled={busy} onClick={() => void remove(r.id)}>Usuń</Button></Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

export default function MappingsPage() {
  const projects = useApi<Rec[]>("/projects");
  return (
    <>
      <PageTitle title="Mapowania" subtitle="Zatwierdzone słowniki i presety, używane ponownie przy kolejnych projektach. Reguły dla konkretnego pliku edytujesz w kreatorze projektu." />
      <div className="space-y-4">
        <Card title="Projekty — edycja reguł">
          {projects.loading ? <Spinner /> : !projects.data?.length ? <Empty>Brak projektów.</Empty> : (
            <Table head={["Projekt", "Rynek", "Szablon", "Status", "Utworzono"]}>
              {projects.data.map((p) => (
                <tr key={p.id}>
                  <Td><Link className="text-indigo-700 hover:underline" href={`/kreator/${p.id}`}>{p.name}</Link></Td>
                  <Td>{MARKET_NAMES[p.marketplace_id]}</Td><Td>{p.template_name} (wersja {p.template_version})</Td>
                  <Td><StatusBadge status={p.status} /></Td><Td className="text-xs text-slate-500">{fmtDate(p.created_at)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <Section title="Presety reguł" path="/presets" head={["Nazwa", "Rynek", "Reguł", "Zapisano"]} empty="Brak presetów. Zapisz preset w edytorze reguł projektu."
          row={(r) => [r.name, MARKET_NAMES[r.marketplace_id], r.rule_count, fmtDate(r.created_at)]} />
        <Section title="Słownik kategorii (zatwierdzone aliasy)" path="/category-mappings" head={["Kategoria w źródle", "Rynek", "Kod kategorii rynku", "Zatwierdzono"]}
          empty="Brak zatwierdzonych aliasów kategorii." row={(r) => [r.source_category, MARKET_NAMES[r.marketplace_id], r.target_code, fmtDate(r.created_at)]} />
        <Section title="Słownik wartości" path="/value-mappings" head={["Wartość w danych", "Rynek", "Pole rynku", "Wartość rynku", "Zatwierdzono"]}
          empty="Brak wpisów słownika wartości." row={(r) => [r.source_value, MARKET_NAMES[r.marketplace_id], r.target_column, r.target_value, fmtDate(r.created_at)]} />
        <Section title="Profile importu (mapowanie kolumn źródła)" path="/profiles" head={["Nazwa", "Kolumn", "Zapisano"]}
          empty="Brak profili. Zapisz profil przy zatwierdzaniu kolumn importu." row={(r) => [r.name, Object.keys(r.column_map).length, fmtDate(r.created_at)]} />
      </div>
    </>
  );
}
