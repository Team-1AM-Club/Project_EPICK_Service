import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import openapiTS, { astToString } from "openapi-typescript";

const frontendRoot = path.resolve(import.meta.dirname, "..");
const repoRoot = path.resolve(frontendRoot, "..");
const schemaPath = path.join(frontendRoot, "generated", "w1-openapi.json");
const outputPath = path.join(frontendRoot, "generated", "w1-api.d.ts");
const temporaryRoot = mkdtempSync(path.join(tmpdir(), "epick-openapi-"));
const repositoryPython = path.join(
  repoRoot,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const python = process.env.EPICK_PYTHON ?? (existsSync(repositoryPython) ? repositoryPython : "python");

try {
  execFileSync(
    python,
    ["-m", "scripts.export_openapi", "--output", schemaPath],
    { cwd: path.join(repoRoot, "backend"), stdio: "inherit" },
  );
  const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
  const rendered = `${astToString(await openapiTS(schema, { alphabetize: true })).trimEnd()}\n`;
  const candidate = path.join(temporaryRoot, "w1-api.d.ts");
  writeFileSync(candidate, rendered, "utf8");
  if (process.argv.includes("--check")) {
    if (readFileSync(outputPath, "utf8") !== rendered) {
      console.error("Generated W1 API types are out of date. Run npm run api:generate.");
      process.exitCode = 1;
    }
  } else {
    writeFileSync(outputPath, rendered, "utf8");
  }
} finally {
  rmSync(temporaryRoot, { recursive: true, force: true });
}
