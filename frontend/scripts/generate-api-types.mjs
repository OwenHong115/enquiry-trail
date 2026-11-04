// Generate src/lib/api-types.ts from the FastAPI OpenAPI schema.
//
// One command (from frontend/): npm run generate:api
// 1. Exports the schema offline via backend/scripts/export_openapi.py
//    (mock settings; no database connection, no provider calls).
// 2. Runs the pinned openapi-typescript generator over the export.
// Re-running must leave src/lib/api-types.ts unchanged. Delivery
// verification re-runs this command and checks the git diff stays
// clean; the CI workflow does not run the generator itself.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontendDir = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = join(frontendDir, "..");
const backendDir = join(repoRoot, "backend");

const pythonCandidates = [
  process.env.PYTHON,
  join(backendDir, ".venv", "Scripts", "python.exe"),
  join(backendDir, ".venv", "bin", "python"),
  "python",
].filter(Boolean);
const python = pythonCandidates.find(
  (candidate) => candidate === "python" || existsSync(candidate),
);

// The temp dir lives under frontend/: some sandboxes deny child
// processes writes to the OS temp directory.
const workDir = mkdtempSync(join(frontendDir, ".openapi-tmp-"));
try {
  const schemaPath = join(workDir, "openapi.json");
  execFileSync(
    python,
    [join(backendDir, "scripts", "export_openapi.py"), schemaPath],
    { cwd: backendDir, stdio: "inherit" },
  );
  const generator = join(
    frontendDir,
    "node_modules",
    "openapi-typescript",
    "bin",
    "cli.js",
  );
  execFileSync(
    process.execPath,
    [generator, schemaPath, "-o", join(frontendDir, "src", "lib", "api-types.ts")],
    { cwd: frontendDir, stdio: "inherit" },
  );
} finally {
  rmSync(workDir, { recursive: true, force: true });
}
