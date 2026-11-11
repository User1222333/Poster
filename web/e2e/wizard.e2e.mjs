// Test E2E głównego kreatora (Playwright, bez @playwright/test).
// Wymaga działającego API (AUTH_MODE=local, INLINE_WORKER=1) i panelu. Uruchomienie: npm run e2e
import { chromium } from "playwright";
import { mkdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.env.E2E_BASE_URL || "http://localhost:3000";
const SHOTS = process.env.E2E_SCREENSHOTS || "";
const out = join(tmpdir(), `mdb-e2e-${Date.now()}`);
mkdirSync(out, { recursive: true });
const steps = [];

function assert(cond, message) {
  if (!cond) throw new Error(`ASERCJA: ${message}`);
  steps.push(message);
}

async function shot(page, name) {
  if (SHOTS) await page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true });
}

async function register(page, email) {
  await page.goto(`${BASE}/login`);
  await page.getByRole("button", { name: "Nie masz konta? Zarejestruj się" }).click();
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill("haslo-testowe-1");
  await page.getByRole("button", { name: "Zarejestruj się", exact: true }).click();
  await page.getByRole("heading", { name: "Panel" }).waitFor();
}

const browser = await chromium.launch();
const failures = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const page = await context.newPage();
  page.on("pageerror", (e) => failures.push(`pageerror: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) failures.push(`console: ${m.text()}`); });
  const stamp = Date.now();

  // 0. Bez logowania panel przekierowuje na /login
  await page.goto(`${BASE}/importy`);
  await page.waitForURL("**/login");
  assert(true, "niezalogowany użytkownik trafia na ekran logowania");

  await register(page, `e2e-${stamp}@example.com`);
  await page.getByRole("link", { name: "Nowy projekt" }).click();

  // 1. Źródło
  await page.getByRole("button", { name: /Użyj danych demonstracyjnych/ }).click();
  await page.getByText("ZAS-001").first().waitFor();
  assert(await page.getByText("Wierszy danych:").innerText().then((t) => t.includes("20")), "krok 1: odczytano 20 wierszy źródła");
  assert((await page.getByText("0012345000010").count()) > 0, "krok 1: EAN z zerami wiodącymi widoczny bez utraty zer");
  await shot(page, "1-zrodlo");
  await page.getByTestId("next-step").click();

  // 2. Marketplace i szablon
  await page.getByRole("button", { name: /użyj fikcyjnego szablonu DEMO/ }).click();
  await page.getByText("1. Struktura szablonu").waitFor();
  assert((await page.getByText(/Wskaż wiersz z kodami kolumn/).count()) > 0, "krok 2: kreator zgłasza niejednoznaczny wiersz nagłówka");
  assert(await page.getByTestId("tpl-confirm").isDisabled(), "krok 2: nie da się zatwierdzić szablonu z niejednoznacznościami");
  await page.getByTestId("tpl-header-row").fill("2");
  await page.getByTestId("tpl-label-row").fill("1");
  await page.getByTestId("tpl-data-row").fill("3");
  await page.getByTestId("tpl-dict-sheet").selectOption("Listy");
  await page.getByTestId("tpl-attr-proposal").click();
  await page.getByTestId("tpl-save-structure").click();
  await page.locator('[data-testid="tpl-category-col"] option[value="demo_kategoria"]').waitFor({ state: "attached" });
  await page.getByTestId("tpl-category-col").selectOption("demo_kategoria");
  await page.getByTestId("tpl-save-structure").click();
  await page.getByText(/Struktura jest jednoznaczna: 13 kolumn/).waitFor();
  await page.getByText("Wymagalność określona dla wszystkich kolumn").waitFor();
  await page.getByTestId("tpl-kind").selectOption("products");
  await page.getByTestId("tpl-banned").fill("kup teraz");
  await page.getByTestId("tpl-html").fill("p, b, br");
  await page.getByTestId("tpl-save-reqs").click();
  await page.getByTestId("tpl-confirm").click();
  await page.getByText(/Struktura i wymagania zatwierdzone/).waitFor();
  assert(true, "krok 2: struktura wskazana, profil wymagań zapisany i zatwierdzony");
  await shot(page, "2-szablon");
  await page.getByTestId("next-step").click();

  // 3. Rozpoznanie kolumn
  await page.getByRole("heading", { name: "Rozpoznanie kolumn" }).waitFor();
  assert(await page.getByLabel("Pole dla kolumny nr zapasu").inputValue() === "sku", "krok 3: „nr zapasu” rozpoznane jako SKU");
  assert(await page.getByLabel("Pole dla kolumny Wymiary").inputValue() === "", "krok 3: „Wymiary” pozostają polem niestandardowym");
  await page.getByPlaceholder("np. Eksport Sellasist firmy").fill("Profil E2E");
  await page.getByRole("button", { name: "Zatwierdź mapowanie kolumn" }).click();
  await page.getByText("Błędny EAN: 1").waitFor();
  await page.getByText("Duplikat SKU: 2").waitFor();
  assert(true, "krok 3: po zatwierdzeniu widać błędny EAN i duplikat SKU ze źródła");
  await shot(page, "3-kolumny");
  await page.getByTestId("next-step").click();

  // 4. Kategorie i reguły
  await page.waitForURL(/\/kreator\/[0-9a-f-]{36}/);
  const cats = { "Dom/Tekstylia/Zasłony": "DEMO-ZASLONY", "Dom/Tekstylia/Firany": "DEMO-FIRANY", "Dom/Tekstylia/Poszewki": "DEMO-POSZEWKI", "Dom/Dywany": "DEMO-DYWANY", "Dom/Kuchnia/Podkładki": "DEMO-PODKLADKI" };
  for (const [src, code] of Object.entries(cats)) {
    const row = page.getByTestId(`cat-${src}`);
    await row.getByRole("combobox").selectOption(code);
    await row.getByRole("button", { name: "Zatwierdź" }).click();
    await row.getByRole("button", { name: "Usuń przypisanie" }).waitFor();
  }
  await page.getByText("Do przypisania: 2").waitFor();
  assert(true, "krok 4: dwie dwuznaczne kategorie pozostają bez przypisania");
  await page.getByTestId("rules-suggest").click();
  await page.getByText(/Zaproponowano źródło dla \d+ kolumn/).waitFor();
  for (const col of ["demo_szerokosc_cm", "demo_wysokosc_cm"]) {
    const rule = page.getByTestId(`rule-${col}`);
    await rule.getByRole("button", { name: "+ dodaj transformację" }).click();
    await rule.getByLabel("Typ kroku").nth(1).selectOption("normalize_units");
    await rule.getByLabel("Jednostka, gdy brak w danych").selectOption("cm");
  }
  const opis = page.getByTestId("rule-demo_opis");
  await opis.getByRole("button", { name: "+ dodaj transformację" }).click();
  await opis.getByLabel("Typ kroku").nth(1).selectOption("transform_html");
  await opis.getByRole("button", { name: "+ dodaj transformację" }).click();
  await opis.getByLabel("Typ kroku").nth(2).selectOption("sanitize_description");
  await page.getByTestId("rules-preview").click();
  await page.getByTestId("preview-demo_szerokosc_cm").waitFor();
  assert(/ZAS-001: 140\s*ZAS-002: 140\s*ZAS-003: 140/.test(await page.getByTestId("preview-demo_szerokosc_cm").innerText()), "krok 4: podgląd — „140 cm”, „140” i „1,4 m” dają 140");
  const prev = await page.getByTestId("preview-demo_opis").innerText();
  assert(/Zasłona blackout\. Idealna do sypialni\./.test(prev) && !/kup teraz/i.test(prev), "krok 4: podgląd — „kup teraz” usunięte z opisu");
  await page.getByTestId("rules-save").click();
  await page.getByText(/Zapisano reguły \(wersja 1\)/).waitFor();
  await page.getByTestId("preset-name").fill("Preset E2E");
  await page.getByTestId("preset-save").click();
  await page.getByText(/Zapisano preset „Preset E2E”/).waitFor();
  assert(true, "krok 4: reguły zapisane (wersja 1) i preset utworzony");
  await shot(page, "4-reguly");
  await page.getByTestId("next-step").click();

  // 5. Walidacja
  await page.getByTestId("run-validation").click();
  await page.getByTestId("counters").waitFor();
  const groups = page.getByTestId("issue-groups");
  await groups.waitFor();
  const text = await groups.innerText();
  assert(/Brak wymaganej wartości: Skład materiału \[DEMO\].*— 2 produkty/s.test(text), "krok 5: „brak składu materiału — 2 produkty” (konkret, nie ogólny błąd)");
  assert(/GTIN_INVALID/.test(text) && /DUPLICATE_SKU/.test(text) && /CATEGORY_UNMAPPED/.test(text), "krok 5: błędny EAN, duplikat SKU i nieprzypisane kategorie zgłoszone");
  await shot(page, "5-walidacja");
  const val = page.getByTestId("val-ciemnozielony");
  await val.getByRole("button", { name: /zielony/ }).first().click();
  await val.getByRole("button", { name: "Zatwierdź" }).click();
  await page.getByTestId("val-ciemnozielony").waitFor({ state: "detached" });
  assert(true, "krok 5: wpis słownika wartości (ciemnozielony → zielony) zatwierdzony z podpowiedzi");
  await page.getByTestId("run-validation").waitFor();
  await page.getByRole("button", { name: /Brak wymaganej wartości: Skład materiału/ }).click();
  await page.getByTestId("bulk-value").fill("100% poliester");
  await page.getByTestId("bulk-preview").click();
  await page.getByText(/Zmiana obejmie 2 rekordy/).waitFor();
  await page.getByTestId("bulk-apply").click();
  await page.getByRole("button", { name: /Brak wymaganej wartości: Skład materiału/ }).waitFor({ state: "detached" });
  assert(true, "krok 5: poprawka masowa z podglądem (2 rekordy) usunęła zgłoszenie po rewalidacji");
  const ready = Number(await page.getByTestId("counters").locator("div", { hasText: /^\d+\s*Gotowe$/ }).first().innerText().then((t) => t.match(/\d+/)[0]));
  assert(ready >= 10 && ready < 20, `krok 5: ${ready} wierszy gotowych, pozostałe nadal wymagają decyzji`);
  const pct = await page.getByTestId("readiness").innerText();
  assert(/\d+%/.test(pct) && /Co blokuje zakończenie/i.test(pct), "panel boczny pokazuje procent gotowości i listę blokad");
  await page.getByTestId("next-step").click();

  // 6. Pobierz plik
  await page.getByTestId("export-create").click();
  const item = page.getByTestId("export-item").first();
  await item.getByTestId("download-data").waitFor();
  assert(/Nie można zadeklarować pełnej zgodności/.test(await item.innerText()), "krok 6: eksport na szablonie DEMO nie jest oznaczony jako zgodny");
  const [dl] = await Promise.all([page.waitForEvent("download"), item.getByTestId("download-data").click()]);
  const file = join(out, dl.suggestedFilename());
  await dl.saveAs(file);
  assert(/^DEMO-NIE-WYSYLAC_empik_products_.*_gotowe\.xlsx$/.test(dl.suggestedFilename()), `krok 6: pobrano ${dl.suggestedFilename()}`);
  assert(readFileSync(file).subarray(0, 2).toString() === "PK", "krok 6: pobrany plik jest prawdziwym XLSX");
  const [rep] = await Promise.all([page.waitForEvent("download"), item.getByTestId("download-report").click()]);
  assert(/_raport\.xlsx$/.test(rep.suggestedFilename()), "krok 6: pobrano raport braków");
  await shot(page, "6-eksport");

  // Pozostałe widoki ładują realne dane
  for (const [path, needle] of [["/", "Ostatnie projekty"], ["/importy", "demo_zrodlo_tekstylia.csv"], ["/produkty", "ZAS-001"], ["/marketplace", "Media Expert"],
    ["/szablony", "DEMO — fikcyjny szablon testowy"], ["/mapowania", "Preset E2E"], ["/bledy", "Co wymaga uwagi"], ["/eksporty", "DEMO-NIE-WYSYLAC"], ["/ustawienia", "export.created"]]) {
    await page.goto(`${BASE}${path}`);
    await page.locator("main").getByText(needle).locator("visible=true").first().waitFor();
  }
  assert(true, "wszystkie pozycje menu wyświetlają dane z backendu");
  await shot(page, "7-ustawienia");

  // Druga sesja: profil i preset dostępne bez konfiguracji od zera
  await page.goto(`${BASE}/kreator`);
  await page.getByText("Zastosuj zapisany profil importu").waitFor();
  assert((await page.locator("option", { hasText: "Profil E2E" }).count()) === 1, "kolejny projekt: zapisany profil importu jest dostępny");

  // Izolacja organizacji w UI
  const ctx2 = await browser.newContext();
  const page2 = await ctx2.newPage();
  await register(page2, `e2e-b-${stamp}@example.com`);
  await page2.goto(`${BASE}/importy`);
  await page2.getByText("Brak importów.").waitFor();
  await page2.goto(`${BASE}/eksporty`);
  await page2.getByText("Nie wygenerowano jeszcze żadnego pliku.").waitFor();
  assert(true, "inna organizacja nie widzi importów ani eksportów");

  // Widok mobilny
  const mobile = await browser.newContext({ viewport: { width: 390, height: 800 }, storageState: await context.storageState() });
  await mobile.close();
  assert(failures.length === 0, `brak błędów JS w konsoli (${failures.join(" | ") || "0"})`);
  console.log(steps.map((s) => `  ✓ ${s}`).join("\n"));
  console.log(`\nE2E OK — ${steps.length} asercji`);
} catch (e) {
  console.log(steps.map((s) => `  ✓ ${s}`).join("\n"));
  console.error(`\nE2E FAILED: ${e.message}`);
  if (failures.length) console.error(failures.join("\n"));
  process.exitCode = 1;
} finally {
  await browser.close();
}
