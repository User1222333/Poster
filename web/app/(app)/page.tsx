"use client";

import Link from "next/link";
import { ExportList } from "@/components/ExportPanel";
import { useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Card, Empty, ErrorNote, fmtDate, MARKET_NAMES, PageTitle, Spinner, StatusBadge, Table, Td } from "@/lib/ui";

function Tile({ label, value }: { label: string; value: number }) {
  return <div className="rounded-lg border border-slate-200 bg-white px-4 py-3"><div className="text-2xl font-semibold">{value}</div><div className="text-xs text-slate-600">{label}</div></div>;
}

export default function DashboardPage() {
  const d = useApi<Rec>("/dashboard");
  if (d.loading) return <Spinner />;
  if (!d.data) return <ErrorNote error={d.error} />;
  const by = (rows: Rec[], ...statuses: string[]) => rows.filter((r) => statuses.includes(r.status)).reduce((a, r) => a + r.count, 0);
  const pr = d.data.projects_by_status;
  return (
    <>
      <PageTitle title="Panel" subtitle="Przygotuj katalog produktów do szablonu marketplace — regułami i słownikami, bez zgadywania."
        actions={<Link href="/kreator" className="rounded-md bg-indigo-600 px-3.5 py-2 text-sm font-medium text-white hover:bg-indigo-700">Nowy projekt</Link>} />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Tile label="Importy (30 dni)" value={d.data.imports_last_30d} />
        <Tile label="Projekty gotowe" value={by(pr, "ready", "completed")} />
        <Tile label="Wymagają decyzji" value={by(pr, "needs_review", "needs_mapping")} />
        <Tile label="Z błędem przetwarzania" value={by(pr, "failed") + by(d.data.imports_by_status, "failed")} />
        <Tile label="W trakcie" value={by(pr, "queued", "validating", "exporting")} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Ostatnie projekty">
          {d.data.recent_projects.length === 0 ? <Empty>Brak projektów. Zacznij od „Nowy projekt”.</Empty> : (
            <Table head={["Projekt", "Rynek", "Status", "Gotowe / błędy / decyzje", "Utworzono"]}>
              {d.data.recent_projects.map((p: Rec) => (
                <tr key={p.id}>
                  <Td><Link className="text-indigo-700 hover:underline" href={`/kreator/${p.id}`}>{p.name}</Link></Td>
                  <Td>{MARKET_NAMES[p.marketplace_id]}</Td><Td><StatusBadge status={p.status} /></Td>
                  <Td>{p.counters.imported != null ? `${p.counters.ready} / ${p.counters.blocked} / ${p.counters.review}` : "—"}</Td>
                  <Td className="text-xs text-slate-500">{fmtDate(p.created_at)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <Card title="Ostatnie importy">
          {d.data.recent_imports.length === 0 ? <Empty>Brak importów.</Empty> : (
            <Table head={["Plik", "Status", "Wierszy", "Wgrano"]}>
              {d.data.recent_imports.map((i: Rec) => (
                <tr key={i.id}>
                  <Td><Link className="text-indigo-700 hover:underline" href={`/importy/${i.id}`}>{i.filename}</Link></Td>
                  <Td><StatusBadge status={i.status} /></Td><Td>{i.row_count}</Td><Td className="text-xs text-slate-500">{fmtDate(i.created_at)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
      <div className="mt-4"><Card title="Ostatnie eksporty"><ExportList /></Card></div>
    </>
  );
}
