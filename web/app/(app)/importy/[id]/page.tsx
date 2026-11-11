"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { ColumnMapper, ImportPreview } from "@/components/ImportSource";
import { PageTitle } from "@/lib/ui";

export default function ImportPage() {
  const { id } = useParams<{ id: string }>();
  const [tick, setTick] = useState(0);
  return (
    <>
      <PageTitle title="Import" subtitle="Odczyt pliku i przypisanie kolumn do pól systemu." />
      <div className="space-y-4">
        <ImportPreview importId={id} onChanged={() => setTick(tick + 1)} />
        <ColumnMapper key={tick} importId={id} />
      </div>
    </>
  );
}
