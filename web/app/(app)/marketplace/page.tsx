"use client";

import Link from "next/link";
import { useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Alert, Card, ErrorNote, PageTitle, Spinner } from "@/lib/ui";

export default function MarketplacePage() {
  const list = useApi<Rec[]>("/marketplaces");
  return (
    <>
      <PageTitle title="Marketplace" subtitle="Rynki oparte na Mirakl. Dla każdego potrzebny jest szablon wygenerowany w panelu sprzedawcy dla Twojej kategorii." />
      <div className="mb-4"><Alert tone="blue">
        Aplikacja nie łączy się z API marketplace i nie zawiera wbudowanych list kolumn, kategorii ani słowników żadnego rynku.
        Gotowy plik wgrywasz samodzielnie w panelu sprzedawcy. Rynek może stosować dodatkową weryfikację katalogową.
      </Alert></div>
      <ErrorNote error={list.error} />
      {list.loading ? <Spinner /> : (
        <div className="grid gap-4 md:grid-cols-3">
          {(list.data || []).map((m) => (
            <Card key={m.id} title={m.name}>
              <p className="text-sm text-slate-600">{m.note}</p>
              <dl className="mt-3 space-y-1 text-sm">
                <div>Wgrane szablony: <strong>{m.templates}</strong></div>
                <div>Projekty: <strong>{m.projects}</strong></div>
              </dl>
              <div className="mt-3 flex gap-3 text-sm">
                <Link className="text-indigo-700 hover:underline" href="/szablony">Szablony</Link>
                <Link className="text-indigo-700 hover:underline" href="/kreator">Nowy projekt</Link>
              </div>
            </Card>
          ))}
        </div>
      )}
    </>
  );
}
