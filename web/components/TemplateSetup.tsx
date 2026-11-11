"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAction, useApi } from "@/lib/hooks";
import type { Rec } from "@/lib/types";
import { Alert, Badge, Button, Card, Empty, ErrorNote, Field, fmtDate, Input, MARKET_NAMES, Select, Spinner, StatusBadge, Table, Td } from "@/lib/ui";

const BUSY = ["queued", "parsing"];
const KINDS: Record<string, string> = { unknown: "— nie określono —", products: "Produkty (karta katalogowa)", offers: "Oferty (cena, ilość, stan)", mixed: "Mieszany (produkty + oferty)" };
const TYPE_LABELS: Record<string, string> = { text: "tekst", number: "liczba", integer: "liczba całkowita", gtin: "EAN/GTIN", url: "adres URL", image_url: "URL zdjęcia", html: "opis (HTML)", enum: "lista wartości" };
const SOURCES: Record<string, string> = { data_validation: "z walidacji w pliku", dictionary_sheet: "z arkusza słowników", attribute_sheet: "z arkusza atrybutów", manual: "zdefiniowane ręcznie" };

export function TemplatePicker({ value, onChange }: { value: string | null; onChange: (id: string) => void }) {
  const markets = useApi<Rec[]>("/marketplaces");
  const [market, setMarket] = useState("empik");
  const list = useApi<Rec[]>(`/templates?marketplace_id=${market}`);
  const [name, setName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const { busy, error, run } = useAction();

  async function upload(file: File | undefined) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("marketplace_id", market);
    form.append("name", name.trim() || file.name);
    const tpl = await run(() => api<Rec>("/templates", { form }));
    if (fileRef.current) fileRef.current.value = "";
    if (tpl) { await list.reload(); onChange(tpl.id); }
  }

  async function demo() {
    const tpl = await run(() => api<Rec>("/templates/demo", { json: { marketplace_id: market } }));
    if (tpl) { await list.reload(); onChange(tpl.id); }
  }

  return (
    <Card title="Marketplace i szablon docelowy">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <Field label="Marketplace">
            <Select value={market} onChange={(e) => setMarket(e.target.value)} data-testid="market-select">
              {(markets.data || []).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </Select>
          </Field>
          <Field label="Nazwa szablonu (np. kategoria)" hint="Kolejne wgranie pod tą samą nazwą tworzy nową wersję.">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="np. Tekstylia domowe" />
          </Field>
          <Field label="Wgraj szablon pobrany z panelu sprzedawcy (XLSX lub CSV)"
            hint="Źródłem prawdy jest Twój rzeczywisty szablon. Aplikacja nie zna z góry kolumn ani słowników żadnego rynku.">
            <input ref={fileRef} type="file" accept=".xlsx,.csv" disabled={busy} data-testid="template-file"
              className="block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-indigo-600 file:px-3 file:py-2 file:text-sm file:font-medium file:text-white"
              onChange={(e) => void upload(e.target.files?.[0])} />
          </Field>
          <Button variant="secondary" disabled={busy} onClick={() => void demo()}>Nie mam szablonu — użyj fikcyjnego szablonu DEMO</Button>
          <p className="text-xs text-slate-500">Szablon DEMO to fixture testowy. Wynik oparty na nim nie jest plikiem gotowym do wysłania.</p>
          {busy && <Spinner label="Wysyłanie…" />}
          <ErrorNote error={error} />
        </div>
        <Field label="…albo wybierz wcześniej wgrany szablon">
          <Select value={value || ""} onChange={(e) => e.target.value && onChange(e.target.value)}>
            <option value="">— wybierz —</option>
            {(list.data || []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · wersja {t.version}{t.is_demo_fixture ? " · DEMO" : ""}{t.confirmed ? " · zatwierdzony" : ""} · {fmtDate(t.created_at)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Card>
  );
}

function SheetPreview({ sheet }: { sheet: Rec }) {
  const width = Math.max(0, ...sheet.preview.map((r: string[]) => r.length));
  return (
    <div className="max-h-64 overflow-auto rounded-md border border-slate-200">
      <table className="min-w-max border-collapse text-xs">
        <thead>
          <tr className="bg-slate-50 text-slate-500">
            <th className="px-2 py-1 text-left">wiersz</th>
            {Array.from({ length: width }, (_, i) => <th key={i} className="px-2 py-1 text-left font-normal">kol. {i + 1}</th>)}
          </tr>
        </thead>
        <tbody>
          {sheet.preview.map((row: string[], r: number) => (
            <tr key={r} className="border-t border-slate-100">
              <td className="bg-slate-50 px-2 py-1 font-semibold text-slate-600">{r + 1}</td>
              {Array.from({ length: width }, (_, i) => <td key={i} className="max-w-40 truncate px-2 py-1 font-mono" title={row[i]}>{row[i]}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function num(v: string): number | null {
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export function TemplateEditor({ templateId, onChanged }: { templateId: string; onChanged?: () => void }) {
  const tpl = useApi<Rec>(`/templates/${templateId}`, (d) => BUSY.includes(d.status));
  const [form, setForm] = useState<Rec | null>(null);
  const [patch, setPatch] = useState<Record<string, Rec>>({});
  const [kind, setKind] = useState<string | null>(null);
  const [profile, setProfile] = useState<Rec | null>(null);
  const [ack, setAck] = useState(false);
  const [openEnum, setOpenEnum] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  const stamp = tpl.data ? `${tpl.data.id}:${tpl.data.status}:${JSON.stringify(tpl.data.structure_options)}` : "";

  useEffect(() => {
    const d = tpl.data;
    if (!d || BUSY.includes(d.status) || d.status === "failed") return;
    const s = d.structure;
    const o = d.structure_options || {};
    setForm({
      data_sheet: s.data_sheet, header_row: String(s.header_row), label_row: String(s.label_row || ""),
      data_start_row: String(s.data_start_row),
      category_column: o.category_column === "" ? "__none__" : (s.category_column || "__unset__"),
      dictionary_sheet: s.dictionary_sheet || "", dictionary_layout: o.dictionary_layout || "wide",
      dict_attr: String(o.dictionary_roles?.attribute || ""), dict_code: String(o.dictionary_roles?.value_code || ""),
      dict_label: String(o.dictionary_roles?.value_label || ""),
      attribute_sheet: s.attribute_sheet || "", attribute_header_row: String(o.attribute_header_row || 1),
      attr_code: String(o.attribute_roles?.code || ""), attr_required: String(o.attribute_roles?.required || ""),
      attr_max: String(o.attribute_roles?.max_length || ""),
    });
    setPatch({});
    setKind(null);
    setProfile(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp]);

  if (tpl.loading) return <Spinner />;
  if (!tpl.data) return <ErrorNote error={tpl.error} />;
  const d = tpl.data;
  if (BUSY.includes(d.status)) return <Card title="Szablon"><Spinner label="Szablon jest odczytywany…" /></Card>;
  if (d.status === "failed") return <Card title="Szablon"><Alert>Nie udało się odczytać szablonu: {d.error}</Alert></Card>;
  if (!form) return <Spinner />;

  const s = d.structure;
  const sheets: Rec[] = s.sheets || [];
  const sheetNames = sheets.map((x) => x.name);
  const set = (k: string, v: string) => setForm({ ...form, [k]: v });
  const isXlsx = d.format === "xlsx";
  const prof = profile ?? { banned_phrases: (d.rule_profile?.banned_phrases || []).join(", "), allowed_html_tags: (d.rule_profile?.allowed_html_tags || []).join(", "), image_extensions: (d.rule_profile?.image_extensions || []).join(", ") };
  const effKind = kind ?? d.kind;
  const reqOf = (key: string): Rec => ({ ...d.requirements[key], ...(patch[key] || {}) });
  const unknown = s.columns.filter((c: Rec) => reqOf(c.key).required === null || reqOf(c.key).required === undefined).length;
  const dirty = Object.keys(patch).length > 0 || kind !== null || profile !== null;

  async function saveStructure() {
    const roles = (pairs: [string, string][]) => Object.fromEntries(pairs.filter(([, v]) => num(v)).map(([k, v]) => [k, num(v)]));
    const body: Rec = {
      header_row: num(form!.header_row), label_row: num(form!.label_row) || 0, data_start_row: num(form!.data_start_row),
      category_column: form!.category_column === "__none__" ? "" : form!.category_column === "__unset__" ? null : form!.category_column,
    };
    if (isXlsx) {
      Object.assign(body, {
        data_sheet: form!.data_sheet, dictionary_sheet: form!.dictionary_sheet || null, dictionary_layout: form!.dictionary_layout,
        dictionary_roles: roles([["attribute", form!.dict_attr], ["value_code", form!.dict_code], ["value_label", form!.dict_label]]),
        attribute_sheet: form!.attribute_sheet || null, attribute_header_row: num(form!.attribute_header_row) || 1,
        attribute_roles: roles([["code", form!.attr_code], ["required", form!.attr_required], ["max_length", form!.attr_max]]),
      });
    }
    const res = await run(() => api(`/templates/${templateId}/structure`, { method: "PUT", json: body }));
    if (res) { await tpl.reload(); onChanged?.(); }
  }

  async function saveRequirements() {
    const list = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
    const res = await run(() => api(`/templates/${templateId}/requirements`, {
      method: "PUT",
      json: {
        requirements: patch, kind: effKind,
        rule_profile: { banned_phrases: list(prof.banned_phrases), allowed_html_tags: list(prof.allowed_html_tags), image_extensions: list(prof.image_extensions) },
      },
    }));
    if (res) { setPatch({}); setKind(null); setProfile(null); await tpl.reload(); onChanged?.(); }
  }

  async function confirm() {
    const res = await run(() => api(`/templates/${templateId}/confirm`, { json: { acknowledge_unknown: ack } }));
    if (res) { await tpl.reload(); onChanged?.(); }
  }

  function edit(key: string, change: Rec) {
    setPatch({ ...patch, [key]: { ...(patch[key] || {}), ...change } });
  }

  const proposal = s.attribute_sheet_proposal;
  const previewSheet = sheets.find((x) => x.name === form.data_sheet) || sheets[0];

  return (
    <div className="space-y-4">
      <Card title={<span className="flex flex-wrap items-center gap-2">{d.name} · wersja {d.version} <StatusBadge status={d.status} />
        {d.is_demo_fixture && <Badge tone="amber">DEMO — fixture testowy</Badge>}
        {d.confirmed ? <Badge tone="green">Zatwierdzony</Badge> : <Badge tone="red">Niezatwierdzony</Badge>}</span>}>
        <dl className="grid gap-x-6 gap-y-1 text-sm text-slate-600 sm:grid-cols-2">
          <div>Marketplace: <strong>{MARKET_NAMES[d.marketplace_id] || d.marketplace_id}</strong></div>
          <div>Plik: {d.filename} ({d.format.toUpperCase()})</div>
          <div>Wgrano: {fmtDate(d.created_at)}</div>
          <div className="truncate" title={d.sha256}>SHA-256: <span className="font-mono text-xs">{d.sha256.slice(0, 16)}…</span></div>
        </dl>
        {d.is_demo_fixture && <div className="mt-3"><Alert tone="amber">To jawnie fikcyjny szablon testowy. Kolumny i słowniki nie pochodzą z żadnego marketplace, a wynik nie jest gotowy do wysłania.</Alert></div>}
        {s.lossless_warnings?.length > 0 && (
          <div className="mt-3"><Alert tone="amber">
            Eksport XLSX nie może zostać oznaczony jako w pełni zgodny z tym szablonem:
            <ul className="ml-5 list-disc">{s.lossless_warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
          </Alert></div>
        )}
      </Card>

      <Card title="1. Struktura szablonu">
        {s.ambiguities.length > 0 ? (
          <div className="mb-3 space-y-2">
            {s.ambiguities.map((a: Rec) => <Alert key={a.code} tone="amber">{a.message}</Alert>)}
          </div>
        ) : <div className="mb-3"><Alert tone="green">Struktura jest jednoznaczna: {s.columns.length} kolumn, dane od wiersza {s.data_start_row}.</Alert></div>}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {isXlsx && (
            <Field label="Arkusz danych">
              <Select value={form.data_sheet} onChange={(e) => set("data_sheet", e.target.value)}>
                {sheetNames.map((n) => <option key={n}>{n}</option>)}
              </Select>
            </Field>
          )}
          <Field label="Wiersz z kodami kolumn" hint="Te nazwy trafią do pliku bez zmian.">
            <Input type="number" min={1} value={form.header_row} onChange={(e) => set("header_row", e.target.value)} data-testid="tpl-header-row" />
          </Field>
          <Field label="Wiersz z etykietami (opcjonalnie)">
            <Input type="number" min={0} value={form.label_row} onChange={(e) => set("label_row", e.target.value)} data-testid="tpl-label-row" />
          </Field>
          <Field label="Pierwszy wiersz danych" hint="Wiersze powyżej zostają zachowane w eksporcie.">
            <Input type="number" min={2} value={form.data_start_row} onChange={(e) => set("data_start_row", e.target.value)} data-testid="tpl-data-row" />
          </Field>
          <Field label="Kolumna z identyfikatorem kategorii">
            <Select value={form.category_column} onChange={(e) => set("category_column", e.target.value)} data-testid="tpl-category-col">
              <option value="__unset__">— nie wskazano —</option>
              <option value="__none__">Szablon nie ma kolumny kategorii</option>
              {s.columns.map((c: Rec) => <option key={c.key} value={c.key}>{c.label ? `${c.label} (${c.code})` : c.code}</option>)}
            </Select>
          </Field>
        </div>
        {isXlsx && (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="space-y-2 rounded-md border border-slate-200 p-3">
              <Field label="Arkusz słowników (listy dozwolonych wartości)">
                <Select value={form.dictionary_sheet} onChange={(e) => set("dictionary_sheet", e.target.value)} data-testid="tpl-dict-sheet">
                  <option value="">— brak / nie używaj —</option>
                  {sheetNames.filter((n) => n !== form.data_sheet).map((n) => <option key={n}>{n}</option>)}
                </Select>
              </Field>
              {form.dictionary_sheet && (
                <>
                  <Field label="Układ arkusza słowników">
                    <Select value={form.dictionary_layout} onChange={(e) => set("dictionary_layout", e.target.value)}>
                      <option value="wide">Kolumna = atrybut (nagłówek to kod kolumny, pod nim wartości)</option>
                      <option value="long">Wiersz = wartość (kolumny: kod atrybutu, kod wartości, etykieta)</option>
                    </Select>
                  </Field>
                  {form.dictionary_layout === "long" && (
                    <div className="grid grid-cols-3 gap-2">
                      <Field label="Nr kol.: kod atrybutu"><Input type="number" min={1} value={form.dict_attr} onChange={(e) => set("dict_attr", e.target.value)} /></Field>
                      <Field label="Nr kol.: kod wartości"><Input type="number" min={1} value={form.dict_code} onChange={(e) => set("dict_code", e.target.value)} /></Field>
                      <Field label="Nr kol.: etykieta"><Input type="number" min={1} value={form.dict_label} onChange={(e) => set("dict_label", e.target.value)} /></Field>
                    </div>
                  )}
                  {s.dictionary_sheet === form.dictionary_sheet && <p className="text-xs text-slate-500">Dopasowano listy dla {s.dictionary_matched} kolumn.</p>}
                </>
              )}
            </div>
            <div className="space-y-2 rounded-md border border-slate-200 p-3">
              <Field label="Arkusz opisu atrybutów (wymagalność, limity)">
                <Select value={form.attribute_sheet} onChange={(e) => set("attribute_sheet", e.target.value)} data-testid="tpl-attr-sheet">
                  <option value="">— brak / nie używaj —</option>
                  {sheetNames.filter((n) => n !== form.data_sheet).map((n) => <option key={n}>{n}</option>)}
                </Select>
              </Field>
              {proposal && !form.attribute_sheet && (
                <Button size="sm" variant="secondary" data-testid="tpl-attr-proposal" onClick={() => setForm({
                  ...form, attribute_sheet: proposal.sheet, attribute_header_row: String(proposal.header_row),
                  attr_code: String(proposal.roles.code || ""), attr_required: String(proposal.roles.required || ""), attr_max: String(proposal.roles.max_length || ""),
                })}>Użyj propozycji: arkusz „{proposal.sheet}”</Button>
              )}
              {form.attribute_sheet && (
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Field label="Wiersz nagłówka"><Input type="number" min={1} value={form.attribute_header_row} onChange={(e) => set("attribute_header_row", e.target.value)} /></Field>
                  <Field label="Nr kol.: kod"><Input type="number" min={1} value={form.attr_code} onChange={(e) => set("attr_code", e.target.value)} /></Field>
                  <Field label="Nr kol.: wymagane"><Input type="number" min={1} value={form.attr_required} onChange={(e) => set("attr_required", e.target.value)} /></Field>
                  <Field label="Nr kol.: maks. długość"><Input type="number" min={1} value={form.attr_max} onChange={(e) => set("attr_max", e.target.value)} /></Field>
                </div>
              )}
              {s.attribute_sheet && s.attribute_sheet === form.attribute_sheet && <p className="text-xs text-slate-500">Odczytano opis dla {s.attribute_matched} kolumn.</p>}
            </div>
          </div>
        )}
        <div className="mt-4 space-y-2">
          <div className="text-xs font-medium text-slate-600">Podgląd arkusza „{previewSheet?.name}” (pierwsze wiersze)</div>
          {previewSheet && <SheetPreview sheet={previewSheet} />}
          {[form.dictionary_sheet, form.attribute_sheet].filter(Boolean).map((n) => {
            const sh = sheets.find((x) => x.name === n);
            return sh ? <details key={n} className="text-xs"><summary className="cursor-pointer text-indigo-700">Podgląd arkusza „{n}”</summary><div className="mt-2"><SheetPreview sheet={sh} /></div></details> : null;
          })}
        </div>
        <div className="mt-4"><Button disabled={busy} onClick={() => void saveStructure()} data-testid="tpl-save-structure">Zapisz strukturę i odczytaj ponownie</Button></div>
      </Card>

      <Card title="2. Profil wymagań" actions={unknown > 0 ? <Badge tone="amber">Nieustalone wymagania: {unknown}</Badge> : <Badge tone="green">Wymagalność określona dla wszystkich kolumn</Badge>}>
        <p className="mb-3 text-sm text-slate-600">
          Wymagalność, typy i listy wartości są odczytywane tylko wtedy, gdy plik je faktycznie zawiera. Pozostałe pola mają status
          „nieustalone” — zdefiniuj je na podstawie dokumentacji rynku. Typy oznaczone „propozycja” wynikają wyłącznie z nazwy kolumny.
        </p>
        <div className="mb-4 max-w-md">
          <Field label="Rodzaj szablonu" hint={s.kind_proposal !== "unknown" ? `Propozycja na podstawie nazw kolumn: ${KINDS[s.kind_proposal]}` : "Nie udało się ustalić na podstawie nazw kolumn."}>
            <Select value={effKind} onChange={(e) => setKind(e.target.value)} data-testid="tpl-kind">
              {Object.entries(KINDS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </Field>
        </div>
        <div className="max-h-[28rem] overflow-auto rounded-md border border-slate-200">
          <Table head={["Kolumna szablonu", "Wymagalność", "Typ", "Maks. długość", "Lista wartości", "Źródło"]}>
            {s.columns.map((c: Rec) => {
              const r = reqOf(c.key);
              return (
                <tr key={c.key}>
                  <Td>
                    <div className="font-mono text-xs font-semibold">{c.code}</div>
                    {c.label && <div className="text-xs text-slate-600">{c.label}</div>}
                    {c.comment && <div className="max-w-64 text-xs italic text-slate-500">{c.comment}</div>}
                    {c.key === s.category_column && <Badge tone="blue">kategoria</Badge>}
                  </Td>
                  <Td>
                    <Select aria-label={`Wymagalność ${c.code}`} className="w-36" value={r.required === true ? "1" : r.required === false ? "0" : ""}
                      onChange={(e) => edit(c.key, { required: e.target.value === "" ? null : e.target.value === "1" })}>
                      <option value="">nieustalone</option><option value="1">wymagane</option><option value="0">opcjonalne</option>
                    </Select>
                  </Td>
                  <Td>
                    <Select aria-label={`Typ ${c.code}`} className="w-40" value={r.type || ""} onChange={(e) => edit(c.key, { type: e.target.value || null })}>
                      <option value="">nieustalony</option>
                      {Object.entries(TYPE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </Select>
                    {r.type_source === "name_hint" && !patch[c.key]?.type && <div className="mt-1"><Badge tone="amber">propozycja</Badge></div>}
                  </Td>
                  <Td>
                    <Input aria-label={`Maks. długość ${c.code}`} className="w-24" type="number" min={1} value={r.max_length ?? ""}
                      onChange={(e) => edit(c.key, { max_length: num(e.target.value) })} />
                  </Td>
                  <Td>
                    {r.enum ? <span className="text-xs">{r.enum.length} wartości</span> : <span className="text-xs text-slate-400">brak</span>}{" "}
                    <button className="text-xs text-indigo-700 hover:underline" onClick={() => setOpenEnum(openEnum === c.key ? null : c.key)}>edytuj</button>
                    {openEnum === c.key && (
                      <textarea className="mt-1 block h-32 w-56 rounded-md border border-slate-300 p-1 font-mono text-xs" aria-label={`Lista wartości ${c.code}`}
                        placeholder={"jeden kod w wierszu\nkod | etykieta"}
                        defaultValue={(r.enum || []).map((e: Rec) => (e.label ? `${e.code} | ${e.label}` : e.code)).join("\n")}
                        onBlur={(e) => {
                          const items = e.target.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
                            const [code, ...rest] = l.split("|");
                            return { code: code.trim(), label: rest.join("|").trim() || null };
                          }).filter((x) => x.code);
                          edit(c.key, items.length ? { enum: items, type: "enum" } : { enum: null, type: r.type === "enum" ? null : r.type });
                        }} />
                    )}
                  </Td>
                  <Td className="text-xs text-slate-500">{patch[c.key] ? "zmiana niezapisana" : SOURCES[r.source] || "—"}</Td>
                </tr>
              );
            })}
          </Table>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          <Field label="Zakazane zwroty w opisach" hint="Oddzielone przecinkami, np. kup teraz. Tylko według zasad Twojego rynku.">
            <Input value={prof.banned_phrases} onChange={(e) => setProfile({ ...prof, banned_phrases: e.target.value })} data-testid="tpl-banned" />
          </Field>
          <Field label="Dozwolone znaczniki HTML" hint="np. p, b, br, ul, li. Puste = brak kontroli znaczników.">
            <Input value={prof.allowed_html_tags} onChange={(e) => setProfile({ ...prof, allowed_html_tags: e.target.value })} data-testid="tpl-html" />
          </Field>
          <Field label="Dozwolone formaty zdjęć" hint="np. jpg, png. Puste = brak kontroli rozszerzenia.">
            <Input value={prof.image_extensions} onChange={(e) => setProfile({ ...prof, image_extensions: e.target.value })} />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button disabled={busy || !dirty} onClick={() => void saveRequirements()} data-testid="tpl-save-reqs">Zapisz profil wymagań</Button>
          {dirty && <span className="text-xs text-amber-700">Masz niezapisane zmiany.</span>}
        </div>
      </Card>

      <Card title="3. Zatwierdzenie">
        {d.confirmed ? (
          <Alert tone="green">Struktura i wymagania zatwierdzone {fmtDate(d.confirmed_at)}. Każda zmiana struktury lub wymagań wymaga ponownego zatwierdzenia.</Alert>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-slate-600">Bez zatwierdzenia żaden wiersz nie otrzyma statusu „gotowy”, a wymagania nie będą egzekwowane.</p>
            {unknown > 0 && (
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1" checked={ack} onChange={(e) => setAck(e.target.checked)} data-testid="tpl-ack" />
                <span>Potwierdzam, że {unknown} kolumn z nieustaloną wymagalnością ma być traktowanych jako niewymagane. Sprawdziłem to w dokumentacji rynku.</span>
              </label>
            )}
            <Button disabled={busy || dirty || s.ambiguities.length > 0} onClick={() => void confirm()} data-testid="tpl-confirm">Zatwierdzam strukturę i wymagania</Button>
            {s.ambiguities.length > 0 && <p className="text-xs text-amber-700">Najpierw rozstrzygnij niejednoznaczności struktury.</p>}
            {dirty && <p className="text-xs text-amber-700">Najpierw zapisz zmiany w profilu wymagań.</p>}
          </div>
        )}
        <div className="mt-3"><ErrorNote error={error} /></div>
      </Card>
    </div>
  );
}

export function TemplateEmpty() {
  return <Empty>Wybierz lub wgraj szablon, aby kontynuować.</Empty>;
}
