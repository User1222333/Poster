"use client";

import { useParams } from "next/navigation";
import { Wizard } from "@/components/Wizard";
import { PageTitle } from "@/lib/ui";

export default function ProjectWizardPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <>
      <PageTitle title="Projekt" subtitle="Kategorie i reguły → walidacja → plik gotowy do pobrania." />
      <Wizard key={id} projectId={id} />
    </>
  );
}
