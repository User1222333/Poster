"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { TemplatePicker } from "@/components/TemplateSetup";
import { useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Badge, Card, Empty, fmtDate, MARKET_NAMES, PageTitle, Spinner, StatusBadge, Table, Td } from "@/lib/ui";

export default function TemplatesPage() {
  const router = useRouter();
  const list = useApi<Rec[]>("/templates");
  return (
    <>
      <PageTitle title="Szablony" subtitle="Rzeczywiste szablony pobrane z panelu sprzedawcy — osobno dla każdego rynku i wersji. To one są źródłem prawdy o kolumnach i wymaganiach." />
      <div className="space-y-4">
        <TemplatePicker value={null} onChange={(id) => router.push(`/szablony/${id}`)} />
        <Card title="Wgrane szablony">
          {list.loading ? <Spinner /> : !list.data?.length ? <Empty>Brak szablonów.</Empty> : (
            <Table head={["Szablon", "Rynek", "Wersja", "Rodzaj", "Kolumny", "Status", "Wgrano"]}>
              {list.data.map((t) => (
                <tr key={t.id}>
                  <Td><Link className="text-indigo-700 hover:underline" href={`/szablony/${t.id}`}>{t.name}</Link> {t.is_demo_fixture && <Badge tone="amber">DEMO</Badge>}
                    <div className="font-mono text-xs text-slate-400" title={t.sha256}>{t.sha256.slice(0, 12)}…</div></Td>
                  <Td>{MARKET_NAMES[t.marketplace_id]}</Td><Td>{t.version}</Td><Td>{t.kind}</Td><Td>{t.column_count}</Td>
                  <Td><StatusBadge status={t.status} /> {t.confirmed ? <Badge tone="green">zatwierdzony</Badge> : <Badge tone="red">niezatwierdzony</Badge>}</Td>
                  <Td className="text-xs text-slate-500">{fmtDate(t.created_at)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
