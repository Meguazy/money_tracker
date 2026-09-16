import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

export default function teardown() {
  const repositoryRoot = resolve(__dirname, "../../../..");
  execFileSync(
    "uv",
    [
      "run",
      "--env-file",
      ".env",
      "python",
      "tests/e2e/money_tracker/web/runtime.py",
      "cleanup",
    ],
    { cwd: repositoryRoot, stdio: "inherit" },
  );
}
