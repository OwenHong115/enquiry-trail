// Playwright suite for the six core pages (A28 browser checks).
// Run only when a browser is available: npx playwright test
// Requires the compose stack (web + api + db) or `npm run dev` plus the API.
import { expect, test, type Page } from "@playwright/test";

const base = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";
const username = process.env.E2E_USERNAME ?? "";
const password = process.env.E2E_PASSWORD ?? "";

function requireCredentials() {
  test.skip(!username || !password, "E2E_USERNAME/E2E_PASSWORD not set");
}

async function signIn(page: Page) {
  await page.goto(base);
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText(new RegExp(`signed in as ${username}`, "i"))).toBeVisible();
}

function uniqueMarker(): string {
  return `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
}

async function submitQuote(page: Page): Promise<{ displayNo: string; marker: string }> {
  const marker = uniqueMarker();
  const firstName = `E2E${marker.slice(0, 8)}`;
  await page.getByRole("link", { name: /new quote/i }).click();
  await expect(page.getByRole("heading", { name: /request a quote/i })).toBeVisible();
  await page.getByLabel(/first name/i).fill(firstName);
  await page.getByLabel(/^email$/i).fill(`e2e-${marker}@example.com`);
  await page.getByLabel(/postcode/i).fill("2000");
  await page.getByLabel(/project details/i).fill(`Synthetic browser check ${marker} for a bathroom renovation.`);
  await page.getByLabel(/i understand this is synthetic demo data/i).check();
  await page.getByRole("button", { name: /submit enquiry/i }).click();
  const status = page.getByRole("status");
  await expect(status).toContainText(/accepted/i);
  const text = (await status.innerText()).trim();
  const match = text.match(/Enquiry\s+(ENQ-\d+)\s+accepted/i);
  if (!match) throw new Error(`Could not read display number from receipt: ${text}`);
  return { displayNo: match[1], marker };
}

test("login page has labelled fields and synthetic badge", async ({ page }) => {
  await page.goto(base);
  await expect(page.getByLabel(/username/i)).toBeVisible();
  await expect(page.getByLabel(/password/i)).toBeVisible();
  await expect(page.getByText(/synthetic demo data/i).first()).toBeVisible();
});

test("six core routes are reachable after sign-in", async ({ page }) => {
  test.skip(!process.env.E2E_USERNAME, "E2E_USERNAME/E2E_PASSWORD not set");
  await page.goto(base);
  await page.getByLabel(/username/i).fill(process.env.E2E_USERNAME ?? "");
  await page.getByLabel(/password/i).fill(process.env.E2E_PASSWORD ?? "");
  await page.getByRole("button", { name: /sign in/i }).click();
  for (const route of ["#/quote", "#/enquiries", "#/tasks", "#/integrations"]) {
    await page.goto(`${base}/${route}`);
    await expect(page.locator("main")).toBeVisible();
  }
});

test("signed-in flow: quote to enquiries to detail to completed task survives reload", async ({ page }) => {
  requireCredentials();
  await signIn(page);
  const { displayNo } = await submitQuote(page);

  // Enquiry appears in the list.
  await page.getByRole("link", { name: /^enquiries$/i }).click();
  await expect(page.getByRole("heading", { name: /^enquiries$/i })).toBeVisible();
  const enquiryLink = page.getByRole("link", { name: displayNo, exact: true });
  await expect(enquiryLink).toBeVisible();

  // Open its detail, confirm badge and synced/ready state via polling.
  await enquiryLink.click();
  await expect(page.getByRole("heading", { name: new RegExp(`enquiry ${displayNo}`, "i") })).toBeVisible();
  await expect(page.getByText(/synthetic demo data/i).first()).toBeVisible();
  await expect(page.getByText(/^ready \(attention: none\)$/i)).toBeVisible({ timeout: 90_000 });
  await expect(page.getByText(/synced/i).first()).toBeVisible();

  // Complete the follow-up task from the Tasks page.
  await page.getByRole("link", { name: /^tasks$/i }).click();
  await expect(page.getByRole("heading", { name: /follow-up tasks/i })).toBeVisible();
  const taskCard = page.locator("article", { hasText: displayNo });
  await expect(taskCard).toBeVisible();
  await taskCard.getByLabel(/reason \(10-500 characters\)/i).fill("Initial call completed with the customer.");
  await taskCard.getByRole("button", { name: /complete task/i }).click();

  // Completed state is shown under the Completed filter.
  await page.getByLabel(/task status filter/i).selectOption("completed");
  const completedCard = page.locator("article", { hasText: displayNo });
  await expect(completedCard).toBeVisible();
  await expect(completedCard).toContainText(/completed/i);

  // Completed state survives a reload.
  await page.reload();
  await expect(page.getByRole("heading", { name: /follow-up tasks/i })).toBeVisible();
  await page.getByLabel(/task status filter/i).selectOption("completed");
  const afterReload = page.locator("article", { hasText: displayNo });
  await expect(afterReload).toBeVisible();
  await expect(afterReload).toContainText(/completed/i);
});

test("polling pauses while the page is hidden and resumes when visible", async ({ page }) => {
  requireCredentials();
  await signIn(page);
  await page.getByRole("link", { name: /^enquiries$/i }).click();
  await expect(page.getByRole("heading", { name: /^enquiries$/i })).toBeVisible();

  let pollingRequests = 0;
  page.on("request", (req) => {
    if (req.url().includes("/api/v1/enquiries")) pollingRequests += 1;
  });

  // Hide the page: the app listens for visibilitychange and stops polling.
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  pollingRequests = 0;
  await page.waitForTimeout(12_000);
  expect(pollingRequests).toBe(0);

  // Show the page again: polling resumes with an immediate refresh.
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => pollingRequests, { timeout: 15_000 }).toBeGreaterThan(0);
});

test("draft edit is retained after a forced 409 conflict", async ({ page, browser }) => {
  requireCredentials();
  await signIn(page);
  const { displayNo } = await submitQuote(page);
  await page.getByRole("link", { name: /view enquiry status/i }).click();
  await expect(page.getByRole("heading", { name: new RegExp(`enquiry ${displayNo}`, "i") })).toBeVisible();
  await expect(page.getByLabel(/draft subject/i)).toBeVisible();

  // A second page in the same signed-in context saves the draft first,
  // moving the draft version forward deterministically.
  const second = await page.context().newPage();
  await second.goto(page.url());
  await expect(second.getByLabel(/draft subject/i)).toBeVisible();
  await second.getByLabel(/draft subject/i).fill("Second editor subject");
  await second.getByLabel(/draft body/i).fill("Second editor body saved first.");
  await second.getByRole("button", { name: /save draft/i }).click();
  await expect(second.getByRole("status")).toContainText(/draft saved/i);
  await second.close();

  // The first page still holds the stale version; its edit must be retained on 409.
  const retainedSubject = `Retained subject ${uniqueMarker()}`;
  const retainedBody = `Retained body ${uniqueMarker()} must stay in the form after a conflict.`;
  await page.getByLabel(/draft subject/i).fill(retainedSubject);
  await page.getByLabel(/draft body/i).fill(retainedBody);
  await page.getByRole("button", { name: /save draft/i }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByLabel(/draft subject/i)).toHaveValue(retainedSubject);
  await expect(page.getByLabel(/draft body/i)).toHaveValue(retainedBody);
});
