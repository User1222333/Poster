"use client";

import { ExportList } from "@/components/ExportPanel";
import { Card, PageTitle } from "@/lib/ui";

export default function ExportsPage() {
  return (
    <>
      <PageTitle title="Eksporty" subtitle="Wygenerowane pliki i raporty braków. Pliki mają ograniczony czas życia i są dostępne tylko dla członków organizacji." />
      <Card><ExportList /></Card>
    </>
  );
}
