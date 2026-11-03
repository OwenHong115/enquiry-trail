import { defineConfig } from "@playwright/test";

// Demo recording config, separate from playwright.config.ts so the
// CI suite keeps its retain-on-failure diagnostics. This config
// always keeps video for a successful demo run.
//
// Required environment:
//   E2E_USERNAME / E2E_PASSWORD  demo operator credentials
//   DEMO_BASE_URL                demo UI origin
//                                (default http://127.0.0.1:15173)
// Output: ../demo-results/<test>/video.webm (gitignored artefacts;
// copy the file to the delivery location named in the runbook).
export default defineConfig({
  testDir: ".",
  testMatch: "demo.spec.ts",
  timeout: 180_000,
  workers: 1,
  use: {
    baseURL: process.env.DEMO_BASE_URL ?? "http://127.0.0.1:15173",
    video: "on",
    trace: "off",
  },
  outputDir: "../demo-results",
});
