"use client";

import { useEffect, useState } from "react";
import { CategoryMapper } from "@/components/Dictionaries";
import { IssuesCenter } from "@/components/IssuesCenter";
import { useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Card, Empty, Field, PageTitle, Select, Spinner } from "@/lib/ui";

export default function IssuesPage() {
  const projects = useApi<Rec[]>("/projects");
  const [projectId, setProjectId] = useState("");
  useEffect(() => {
    if (!projectId && projects.data?.length) setProjectId(projects.data[0].id);
  }, [projects.data, projectId]);
  return (
    <>
      <PageTitle title="Centrum błędów" subtitle="Rzeczywiste problemy w danych: czego brakuje, w ilu produktach i w którym polu. Poprawki pojedyncze i masowe, zawsze z podglądem." />
      {projects.loading ? <Spinner /> : !projects.data?.length ? <Empty>Brak projektów. Utwórz projekt w kreatorze.</Empty> : (
        <div className="space-y-4">
          <Card>
            <div className="max-w-xl">
              <Field label="Projekt">
                <Select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
                  {projects.data.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select>
              </Field>
            </div>
          </Card>
          {projectId && <IssuesCenter key={projectId} projectId={projectId} />}
          {projectId && <CategoryMapper key={`c-${projectId}`} projectId={projectId} />}
        </div>
      )}
    </>
  );
}
