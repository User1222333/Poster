"use client";

import { Wizard } from "@/components/Wizard";
import { PageTitle } from "@/lib/ui";

export default function NewWizardPage() {
  return (
    <>
      <PageTitle title="Nowy projekt" subtitle="Jeden projekt = jeden plik źródłowy przetworzony dla wybranego rynku i szablonu." />
      <Wizard />
    </>
  );
}
