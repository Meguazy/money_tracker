import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

const routes = [
  ["/", "Dove stanno andando i tuoi soldi"],
  ["/analisi", "Guarda la spesa nel tempo"],
  ["/consigli", "Consigli verificabili sui tuoi dati"],
  ["/movimenti", "Movimenti"],
  ["/categorie", "Categorie"],
  ["/fonti", "Da dove arrivano i fondi"],
  ["/budget", "Quanto vuoi spendere"],
  ["/scontrini", "Scontrini"],
  ["/messaggistica", "Messaggistica"],
  ["/impostazioni", "Impostazioni"],
] as const;

async function waitForRoute(page: Page, path: string, heading: string) {
  await page.goto(path);
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  await page.waitForLoadState("networkidle");
}

async function expectAccessible(page: Page) {
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(result.violations).toEqual([]);
}

async function expectAccessibleWhileHovered(page: Page, target: Locator) {
  // Axe evaluates the current computed style, so hovering first puts the
  // interactive colour state, not only the resting one, under the audit.
  const background = () =>
    target.evaluate((element) => getComputedStyle(element).backgroundColor);
  // A control revealed by a click can appear under the pointer already.
  await page.mouse.move(0, 0);
  await expect.poll(background).not.toBe("");
  const resting = await background();
  await target.hover();
  await expect.poll(background).not.toBe(resting);
  await expectAccessible(page);
}

function romeMonth(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    timeZone: "Europe/Rome",
  }).formatToParts(new Date());
  const value = (type: string) => parts.find((part) => part.type === type)?.value;
  return `${value("year")}-${value("month")}`;
}

function euro(minorUnits: number): string {
  return new Intl.NumberFormat("it-IT", { style: "currency", currency: "EUR" }).format(
    minorUnits / 100,
  );
}

function card(page: Page, text: string): Locator {
  return page.locator(".editor-card").filter({
    has: page.getByRole("heading", { name: text, exact: true }),
  });
}

function managedCategoryCard(page: Page, text: string): Locator {
  // Proposal cards share the class, and a managed card loses its "Rinomina"
  // button while it is edited or once archived, so scope by the section.
  return page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "Gestisci nomi e disponibilità" }),
    })
    .locator(".editor-card")
    .filter({ has: page.getByText(text, { exact: true }) });
}

test.describe.configure({ mode: "serial" });

test("supports direct navigation, reload, keyboard use, and automated accessibility", async ({
  page,
}) => {
  for (const [path, heading] of routes) {
    await waitForRoute(page, path, heading);
    await expectAccessible(page);
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  }

  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Vai al contenuto" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Quadro", exact: true })).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await page.getByRole("link", { name: "Analisi", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { level: 1, name: "Guarda la spesa nel tempo" }),
  ).toBeVisible();
});

test("announces loading and recovers from a browser transport failure", async ({ page }) => {
  let firstRequest = true;
  await page.route("**/api/v1/transactions?*", async (route) => {
    if (firstRequest) {
      firstRequest = false;
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.abort("connectionfailed");
      return;
    }
    await route.continue();
  });

  await page.goto("/movimenti");
  await expect(page.getByText("Caricamento dei movimenti…")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("Registro non disponibile");
  await page.getByRole("button", { name: "Riprova" }).click();
  await expect(page.getByText("Bozza E2E per Giardinaggio E2E")).toBeVisible();
});

test("runs the manual movement, budget, analytics, and cancellation workflow", async ({
  page,
}) => {
  await waitForRoute(page, "/movimenti", "Movimenti");
  await page.getByLabel("Importo in euro").fill("42,50");
  await page.getByLabel("Descrizione").fill("Spesa E2E supermercato");
  await page.getByLabel("Esercente, facoltativo").fill("Mercato E2E");
  await page
    .getByRole("combobox", { name: /^Categoria 1/ })
    .selectOption({ label: "Spesa alimentare" });
  await page.getByLabel("Importo categoria 1 in euro").fill("42,50");
  await page.getByLabel("Fonte di pagamento 1").selectOption({ label: "Carta E2E" });
  await page.getByLabel("Importo fonte 1 in euro").fill("42,50");
  await expect(page.getByText(/Somma categorie:.*Riconciliata/)).toBeVisible();
  await expect(page.getByText(/Somma fonti:.*Riconciliata/)).toBeVisible();
  await page.getByRole("button", { name: "Salva come bozza" }).click();

  let movement = page.getByRole("row").filter({ hasText: "Spesa E2E supermercato" });
  await expect(movement).toContainText("Bozza");
  await movement.getByRole("button", { name: "Registra" }).click();
  movement = page.getByRole("row").filter({ hasText: "Spesa E2E supermercato" });
  await expect(movement).toContainText("Registrato");

  await waitForRoute(page, "/budget", "Quanto vuoi spendere");
  await page.getByLabel("Categoria").selectOption({ label: "Spesa alimentare" });
  await page.getByPlaceholder("400,00").fill("40,00");
  await page.getByRole("button", { name: "Salva budget" }).click();
  const budgetRow = page.getByRole("row").filter({ hasText: "Spesa alimentare" });
  await expect(budgetRow).toContainText("40,00");
  await expect(budgetRow).toContainText("42,50");
  await expect(budgetRow).toContainText("-2,50");

  await waitForRoute(page, "/", "Dove stanno andando i tuoi soldi");
  await expect(page.getByText("42,50 €", { exact: true }).first()).toBeVisible();
  const chart = page.getByRole("img", { name: /Confronto tra spesa registrata/ });
  await expect(chart).toBeVisible();
  await chart.focus();
  await expect(chart).toBeFocused();
  const overviewBudgetRow = page
    .getByRole("row")
    .filter({ hasText: "Spesa alimentare" });
  await expect(overviewBudgetRow).toContainText("42,50");
  await expectAccessible(page);

  await waitForRoute(page, "/analisi", "Guarda la spesa nel tempo");
  await expect(page.getByRole("rowheader", { name: "Mercato E2E" })).toBeVisible();
  await expect(
    page.getByRole("img", { name: /Totali netti dei movimenti registrati/i }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: /spesa netta per esercente/i }),
  ).toContainText("42,50");

  await waitForRoute(page, "/movimenti", "Movimenti");
  movement = page.getByRole("row").filter({ hasText: "Spesa E2E supermercato" });
  await movement.getByRole("button", { name: "Correggi" }).click();
  await page.getByLabel("Importo in euro").fill("50,00");
  await page.getByLabel("Importo categoria 1 in euro").fill("50,00");
  await page.getByLabel("Importo fonte 1 in euro").fill("50,00");
  await page.getByRole("button", { name: "Salva correzione" }).click();
  movement = page.getByRole("row").filter({ hasText: "Spesa E2E supermercato" });
  await expect(movement).toContainText("50,00");
  page.once("dialog", (dialog) => dialog.accept());
  await movement.getByRole("button", { name: "Annulla" }).click();
  await expect(movement).toContainText("Annullato");

  await waitForRoute(page, "/", "Dove stanno andando i tuoi soldi");
  await expect(page.getByText("Nessun movimento registrato in questo mese.")).toBeVisible();
  await expect(
    page.getByRole("row").filter({ hasText: "Spesa alimentare" }),
  ).toContainText("0,00");
});

test("runs funding, settings, category, receipt, and messaging controls", async ({
  page,
}) => {
  await waitForRoute(page, "/fonti", "Da dove arrivano i fondi");
  await page.getByLabel("Nome").fill("Buoni E2E");
  await page.getByLabel("Classe economica").selectOption("benefit");
  await page.getByLabel("Gestione saldo").selectOption("ledger");
  await page.getByRole("button", { name: "Aggiungi fonte" }).click();
  let sourceCard = card(page, "Buoni E2E");
  await expect(sourceCard).toBeVisible();
  await sourceCard.getByRole("button", { name: "Registra movimento wallet" }).click();
  await page.getByLabel("Tipo").selectOption("opening");
  await page.getByLabel("Importo in euro").fill("30,00");
  await page.getByLabel("Scadenza, facoltativa").fill("2026-12-31");
  await page
    .getByRole("button", { name: "Registra movimento", exact: true })
    .click();
  sourceCard = card(page, "Buoni E2E");
  await expect(sourceCard).toContainText("30,00");

  await waitForRoute(page, "/impostazioni", "Impostazioni");
  await page.getByLabel("Fonte predefinita").selectOption({ label: "Buoni E2E · Benefit" });
  await page.getByRole("button", { name: "Salva fonte predefinita" }).click();
  await page.getByRole("button", { name: "Conferma modifica" }).click();
  await expect(page.getByRole("status")).toContainText(
    "La fonte predefinita è stata aggiornata.",
  );


  await waitForRoute(page, "/categorie", "Categorie");
  const gardening = card(page, "Giardinaggio E2E");
  await gardening.getByRole("button", { name: "Approva e registra" }).click();
  await expect(managedCategoryCard(page, "Giardinaggio E2E")).toContainText("Attiva");
  const teaching = card(page, "Didattica E2E");
  await teaching.getByRole("button", { name: "Usa Formazione" }).click();
  await expect(card(page, "Didattica E2E")).toHaveCount(0);
  const collecting = card(page, "Collezionismo E2E");
  await collecting.getByRole("button", { name: "Annulla proposta e bozza" }).click();
  await expect(card(page, "Collezionismo E2E")).toHaveCount(0);
  const subscriptions = managedCategoryCard(page, "Abbonamenti");
  await subscriptions.getByRole("button", { name: "Rinomina" }).click();
  await subscriptions.getByLabel("Nuovo nome per Abbonamenti").fill("Abbonamenti E2E");
  await subscriptions.getByRole("button", { name: "Salva nome" }).click();
  const renamed = managedCategoryCard(page, "Abbonamenti E2E");
  await renamed.getByRole("button", { name: "Archivia" }).click();
  const confirmArchive = renamed.getByRole("button", { name: "Conferma archiviazione" });
  await expectAccessibleWhileHovered(page, confirmArchive);
  await confirmArchive.click();
  await expect(managedCategoryCard(page, "Abbonamenti E2E")).toContainText("Archiviata");


  await waitForRoute(page, "/fonti", "Da dove arrivano i fondi");
  await expect(card(page, "Buoni E2E")).toContainText("Predefinita");
  let oldSource = card(page, "Carta E2E");
  await oldSource.getByRole("button", { name: "Rinomina" }).click();
  await oldSource.getByLabel("Nuovo nome per Carta E2E").fill("Carta E2E archiviabile");
  await oldSource.getByRole("button", { name: "Salva nome" }).click();
  oldSource = card(page, "Carta E2E archiviabile");
  await oldSource.getByRole("button", { name: "Archivia" }).click();
  await oldSource.getByRole("button", { name: "Conferma archiviazione" }).click();
  await expect(card(page, "Carta E2E archiviabile")).toContainText("Archiviata");

  await waitForRoute(page, "/scontrini", "Scontrini");
  const receiptRow = page.getByRole("row").filter({ hasText: "scontrino-e2e.jpg" });
  await receiptRow
    .getByRole("button", { name: "Apri la revisione di scontrino-e2e.jpg" })
    .click();
  await receiptRow.getByRole("button", { name: "Approva scontrino-e2e.jpg" }).click();
  await expect(receiptRow).toContainText("Approvato");

  await waitForRoute(page, "/messaggistica", "Messaggistica");
  await page.getByRole("button", { name: /Rimetti in coda/ }).click();
  await expect(page.getByText("Nessun lavoro in ingresso ha esaurito i tentativi.")).toBeVisible();
  await page.getByRole("button", { name: /Rinvia/ }).click();
  await expect(
    page.getByText("Nessuna risposta in uscita ha esaurito i tentativi."),
  ).toBeVisible();
});

test("@local-model uses the promoted local model and deterministic advice evidence", async ({ page }) => {
  // The test owns its budget so it also passes when selected on its own.
  await waitForRoute(page, "/budget", "Quanto vuoi spendere");
  await page.getByLabel("Categoria").selectOption({ label: "Veterinario" });
  await page.getByPlaceholder("400,00").fill("25,00");
  await page.getByRole("button", { name: "Salva budget" }).click();
  await expect(page.getByRole("row").filter({ hasText: "Veterinario" })).toContainText(
    "25,00",
  );

  const period = romeMonth();
  const persistedState = async () => {
    const [budgets, transactions] = await Promise.all([
      page.request.get(`/api/v1/budgets?period=${period}`),
      page.request.get(`/api/v1/transactions?period=${period}`),
    ]);
    expect(budgets.ok()).toBeTruthy();
    expect(transactions.ok()).toBeTruthy();
    return { budgets: await budgets.json(), transactions: await transactions.json() };
  };
  const before = await persistedState();

  await waitForRoute(page, "/consigli", "Consigli verificabili sui tuoi dati");
  await page.getByLabel("La tua domanda").fill("Come vanno tutti i miei budget questo mese?");
  const answer = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/advice") &&
      response.request().method() === "POST",
    { timeout: 120_000 },
  );
  await page.getByRole("button", { name: "Consulta i dati" }).click();
  const response = await answer;
  expect(response.status()).toBe(200);
  const advice = await response.json();
  expect(advice.status).toBe("answered");
  expect(advice.evidence.kind).toBe("budgets_at_risk");
  expect(advice.evidence.assessment_status).toBe("budget_evidence");
  expect(
    `${advice.evidence.period.year}-${String(advice.evidence.period.month).padStart(2, "0")}`,
  ).toBe(period);

  // Every figure the model-selected tool returned equals the deterministic plan.
  type Position = {
    category_name: string;
    target: { minor_units: number };
    posted_spend: { minor_units: number };
    remaining: { minor_units: number };
  };
  const figures = (position: Position) => [
    position.category_name,
    position.target.minor_units,
    position.posted_spend.minor_units,
    position.remaining.minor_units,
  ];
  const byName = (left: Position, right: Position) =>
    left.category_name.localeCompare(right.category_name);
  expect([...advice.evidence.positions].sort(byName).map(figures)).toEqual(
    [...before.budgets.entries].sort(byName).map(figures),
  );
  expect(advice.evidence.positions.map(figures)).toContainEqual([
    "Veterinario",
    2500,
    0,
    2500,
  ]);

  await expect(page.getByRole("heading", { name: "Evidenze sui budget" })).toBeVisible();
  const evidence = page.getByRole("region", { name: "Evidenze sui budget" });
  for (const position of advice.evidence.positions as Position[]) {
    const row = evidence
      .getByRole("row")
      .filter({ has: page.getByRole("rowheader", { name: position.category_name }) });
    await expect(row.getByRole("cell")).toHaveText([
      euro(position.target.minor_units),
      euro(position.posted_spend.minor_units),
      euro(position.remaining.minor_units),
    ]);
  }
  await expect(
    page.getByText(/Evidenza derivata dal tool deterministico promosso/),
  ).toBeVisible();
  await expectAccessible(page);

  expect(await persistedState()).toEqual(before);
});
