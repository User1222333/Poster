"use client";

import { useParams } from "next/navigation";
import { TemplateEditor } from "@/components/TemplateSetup";
import { PageTitle } from "@/lib/ui";

export default function TemplatePage() {
  const { id } = useParams<{ id: string }>();
  return (
    <>
      <PageTitle title="Szablon docelowy" subtitle="Wskaż strukturę, zdefiniuj profil wymagań i zatwierdź go." />
      <TemplateEditor templateId={id} />
    </>
  );
}
