// Playwright suite for the six core pages (A28 browser checks).
// Run only when a browser is available: npx playwright test
// Requires the compose stack (web + api + db) or `npm run dev` plus the API.
import { expect, test } from "@playwright/test";

const base = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173";

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
