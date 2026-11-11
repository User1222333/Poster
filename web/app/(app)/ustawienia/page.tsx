"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Badge, Button, Card, ErrorNote, fmtDate, Input, PageTitle, Select, Spinner, Table, Td } from "@/lib/ui";

export default function SettingsPage() {
  const me = useApi<Rec>("/me");
  const members = useApi<Rec[]>("/org/members");
  const audit = useApi<Rec[]>("/audit?limit=50");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const { busy, error, run } = useAction();
  if (me.loading) return <Spinner />;
  if (!me.data) return <ErrorNote error={me.error} />;
  const owner = me.data.role === "owner";

  async function add() {
    const res = await run(() => api("/org/members", { json: { email, role } }));
    if (res) { setEmail(""); await members.reload(); await audit.reload(); }
  }
  async function remove(id: string) {
    const res = await run(() => api(`/org/members/${id}`, { method: "DELETE" }));
    if (res) { await members.reload(); await audit.reload(); }
  }

  return (
    <>
      <PageTitle title="Ustawienia" subtitle="Organizacja, członkowie, limity i dziennik zdarzeń." />
      <div className="space-y-4">
        <Card title="Organizacja">
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div>Nazwa: <strong>{me.data.organization.name}</strong></div>
            <div>Twoje konto: {me.data.email} <Badge>{owner ? "właściciel" : "członek"}</Badge></div>
            <div>Limit pliku: {me.data.limits.max_upload_mb} MB</div>
            <div>Limit wierszy w imporcie: {me.data.limits.max_rows}</div>
            <div>Czas życia plików eksportu: {me.data.limits.export_ttl_hours} h</div>
          </dl>
          <p className="mt-3 text-xs text-slate-500">Dane każdej organizacji są odseparowane w bazie (RLS) i w magazynie plików. Pliki są dostępne wyłącznie przez serwer po sprawdzeniu członkostwa.</p>
        </Card>
        <Card title="Członkowie">
          <ErrorNote error={error || members.error} />
          {members.loading ? <Spinner /> : (
            <Table head={["E-mail", "Rola", "Dodano", ""]}>
              {(members.data || []).map((m) => (
                <tr key={m.user_id}>
                  <Td>{m.email}</Td><Td>{m.role === "owner" ? "właściciel" : "członek"}</Td><Td className="text-xs text-slate-500">{fmtDate(m.created_at)}</Td>
                  <Td>{owner && m.user_id !== me.data!.user_id && <Button size="sm" variant="danger" disabled={busy} onClick={() => void remove(m.user_id)}>Usuń</Button>}</Td>
                </tr>
              ))}
            </Table>
          )}
          {owner && (
            <div className="mt-4 flex flex-wrap items-end gap-2">
              <Input className="w-72" type="email" placeholder="e-mail istniejącego konta" value={email} onChange={(e) => setEmail(e.target.value)} aria-label="E-mail nowego członka" />
              <Select className="w-40" value={role} onChange={(e) => setRole(e.target.value)} aria-label="Rola">
                <option value="member">członek</option><option value="owner">właściciel</option>
              </Select>
              <Button disabled={busy || !email} onClick={() => void add()}>Dodaj do organizacji</Button>
            </div>
          )}
        </Card>
        <Card title="Dziennik zdarzeń (ostatnie 50)">
          {audit.loading ? <Spinner /> : (
            <div className="max-h-96 overflow-auto">
              <Table head={["Czas", "Zdarzenie", "Obiekt", "Szczegóły"]}>
                {(audit.data || []).map((a) => (
                  <tr key={a.id}>
                    <Td className="text-xs text-slate-500">{fmtDate(a.created_at)}</Td><Td className="font-mono text-xs">{a.action}</Td>
                    <Td className="text-xs">{a.entity}</Td><Td className="max-w-md truncate font-mono text-xs" title={JSON.stringify(a.details)}>{JSON.stringify(a.details)}</Td>
                  </tr>
                ))}
              </Table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
