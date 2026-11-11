"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ImportPicker } from "@/components/ImportSource";
import { useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Badge, Card, Empty, fmtDate, PageTitle, Spinner, StatusBadge, Table, Td } from "@/lib/ui";

export default function ImportsPage() {
  const router = useRouter();
  const list = useApi<Rec[]>("/imports");
  return (
    <>
      <PageTitle title="Importy" subtitle="Pliki źródłowe z katalogiem produktów. Każdy plik jest przechowywany bez zmian, a dane wczytywane bezstratnie jako tekst." />
      <div className="space-y-4">
        <ImportPicker value={null} onChange={(id) => router.push(`/importy/${id}`)} />
        <Card title="Wszystkie importy">
          {list.loading ? <Spinner /> : !list.data?.length ? <Empty>Brak importów.</Empty> : (
            <Table head={["Plik", "Format", "Status", "Wierszy", "Kolumny", "Wgrano"]}>
              {list.data.map((i) => (
                <tr key={i.id}>
                  <Td><Link className="text-indigo-700 hover:underline" href={`/importy/${i.id}`}>{i.filename}</Link>{i.error && <div className="text-xs text-red-700">{i.error}</div>}</Td>
                  <Td>{i.format.toUpperCase()}</Td><Td><StatusBadge status={i.status} /></Td><Td>{i.row_count}</Td>
                  <Td>{i.column_map_confirmed ? <Badge tone="green">zatwierdzone</Badge> : <Badge tone="amber">do zatwierdzenia</Badge>}</Td>
                  <Td className="text-xs text-slate-500">{fmtDate(i.created_at)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
