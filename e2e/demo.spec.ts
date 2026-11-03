// Demo recording spec (run with demo.config.ts, never in CI).
// Records the UI operator flow against the isolated demo stack:
// sign in, submit a synthetic quote, watch it sync, open the
// detail timeline, complete the follow-up, and confirm the
// completed state survives a reload. Scenarios 1-3 of the demo
// runbook are API/simulator evidence, not part of this video.
import { expect, test, type Page } from "@playwright/test";

const username = process.env.E2E_USERNAME ?? "";
const password = process.env.E2E_PASSWORD ?? "";

function requireCredentials() {
  if (!username || !password) {
    throw new Error("Demo recording requires E2E_USERNAME and E2E_PASSWORD");
  }
}

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel(/username/i).fill(username);
  await page.getByLabel(/password/i).fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText(new RegExp(`signed in as ${username}`, "i"))).toBeVisible();
}

test("demo: quote to synced enquiry to completed follow-up", async ({ page }) => {
  requireCredentials();
  const marker = `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
  const email = `demo-video-${marker}@example.com`;

  await signIn(page);
  await page.waitForTimeout(1000);

  // Submit a synthetic quote through the real UI.
  await page.getByRole("link", { name: /new quote/i }).click();
  await expect(page.getByRole("heading", { name: /request a quote/i })).toBeVisible();
  await page.getByLabel(/first name/i).fill("DemoVideo");
  await page.getByLabel(/^email$/i).fill(email);
  await page.getByLabel(/postcode/i).fill("2000");
  await page.getByLabel(/project details/i).fill(`Synthetic demo recording ${marker} for a kitchen renovation.`);
  await page.getByLabel(/i understand this is synthetic demo data/i).check();
  await page.getByRole("button", { name: /submit enquiry/i }).click();
  const status = page.getByRole("status");
  await expect(status).toContainText(/accepted/i);
  const receipt = (await status.innerText()).trim();
  const match = receipt.match(/Enquiry\s+(ENQ-\d+)\s+accepted/i);
  if (!match) throw new Error(`Could not read display number from receipt: ${receipt}`);
  const displayNo = match[1];
  const href = await status.getByRole("link", { name: /view enquiry status/i }).getAttribute("href");
  const idMatch = href?.match(/#\/enquiries\/(.+)$/);
  if (!idMatch) throw new Error(`Could not read enquiry id from receipt link: ${href}`);
  const enquiryId = idMatch[1];
  await page.waitForTimeout(1000);

  // Enquiries list via the scoped row, then the detail timeline.
  await page.getByRole("link", { name: /^enquiries$/i }).click();
  const row = page.getByTestId(`enquiry-row-${enquiryId}`);
  await expect(row).toBeVisible();
  await expect(row).toContainText("ready", { timeout: 120_000 });
  await row.getByRole("link", { name: new RegExp(displayNo) }).click();
  await expect(page.getByText(/synthetic demo data/i).first()).toBeVisible();
  await expect(page.getByText(/enquiry\.received/i).first()).toBeVisible();
  await page.waitForTimeout(1500);

  // Complete the follow-up and prove it survives a reload.
  await page.getByRole("link", { name: /^tasks$/i }).click();
  const card = page.getByTestId(`task-row-${enquiryId}`);
  await card.getByLabel(/reason \(10-500 characters\)/i).fill("Demo follow-up completed during the recorded walkthrough.");
  await card.getByRole("button", { name: `Complete follow-up for ${displayNo}` }).click();
  await expect(card).toBeHidden();
  await page.getByLabel(/task status filter/i).selectOption("completed");
  const completed = page.getByTestId(`task-row-${enquiryId}`);
  await expect(completed).toBeVisible();
  await expect(completed).toContainText(/completed/i);
  await page.waitForTimeout(1000);
  await page.reload();
  await page.getByLabel(/task status filter/i).selectOption("completed");
  await expect(page.getByTestId(`task-row-${enquiryId}`)).toBeVisible();
  await page.waitForTimeout(1500);
});
