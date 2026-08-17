// GENERATES the contents of the `mailflat-examples` repo — never hand-written.
//
// Why generated: the example repo and the scaffolder say the same thing. Maintaining both by
// hand would invite exactly the drift we have already fixed twice: the template changes, the
// repo stays behind, nobody notices. Here the repo IS the template output — it cannot drift.
//
// Usage:
//   node scripts/build-examples.mjs [output-dir]      (default: ../../../mailflat-examples)
//
// Output: one directory per template + a root README + a CI workflow that runs the smoke
// tests. Creating and PUSHING the repo is not this script's job — that is an outward-facing
// action the user performs.
//
// Connected to:
//   - imports from: ../src/templates.js (tek kaynak)
//   - imported by:  — (a script run by hand)
//
// Key export: `buildExamples(outDir)` — { root, dirs[], files }

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import process from "node:process";
import { TEMPLATES } from "../src/templates.js";

/** Root README — what each directory is, and where the docs page for it lives. */
function rootReadme() {
  const rows = TEMPLATES.map(
    (t) => `| [\`${t.id}/\`](./${t.id}) | ${t.label} — ${t.blurb} | \`${t.runner}\` |`,
  );
  return [
    "# mailflat-examples",
    "",
    "Working email-testing projects, one per runner. Each one opens a real",
    "[MailFlat](https://mailflat.net) inbox, reads the code your app sent, and cleans up.",
    "",
    "| Example | What it shows | Run |",
    "|---|---|---|",
    ...rows,
    "",
    "## Start here",
    "",
    "```bash",
    "git clone https://github.com/MailFlat/mailflat-examples",
    "cd mailflat-examples/playwright",
    "cp .env.example .env      # paste your key",
    "npm install && npm test",
    "```",
    "",
    "Or skip the clone and generate the same project fresh:",
    "",
    "```bash",
    "npm create mailflat@latest",
    "```",
    "",
    "## What each example contains",
    "",
    "Two tests, on purpose:",
    "",
    "- **`smoke`** runs green the moment you have a key. It opens a real inbox, checks the",
    "  address came back, and deletes it — so a red smoke test means the key or the network,",
    "  never your app.",
    "- **`signup`** is the one you edit, and it is **skipped until `APP_URL` is set**. A signup",
    "  test that passes without touching your app proves nothing.",
    "",
    "## Other runners",
    "",
    "Cypress, Selenium, Jest, Cucumber, Robot Framework, WebdriverIO, TestCafe, Postman,",
    "GitHub Actions and GitLab CI are covered on the docs site rather than here — each is a",
    "single page with a complete working example:",
    "",
    "<https://mailflat.net/docs/automation>",
    "",
    "## Generated, not hand-written",
    "",
    "These projects are the output of",
    "[`create-mailflat`](https://www.npmjs.com/package/create-mailflat). Do not edit them",
    "here — change the templates in the MailFlat repo and regenerate, or the two drift apart.",
    "",
    "## License",
    "",
    "MIT",
    "",
  ].join("\n");
}

/** CI that really runs the smoke tests — the job skips when the repo secret has no key. */
function ciWorkflow() {
  return [
    "# Runs the smoke test of every example against the real API.",
    "# Without the MAILFLAT_API_KEY secret the job skips instead of failing: a fork should",
    "# not go red for not having our key.",
    "name: examples",
    "",
    "on:",
    "  push:",
    "  pull_request:",
    "  schedule:",
    "    - cron: \"0 6 * * 1\"   # weekly — catches SDK drift before a user does",
    "",
    "# NOTE: the `secrets` context is NOT available in a step-level `if:` (GitHub only exposes",
    "# github/env/matrix/...). So the secret is lifted into env at job level and checked via",
    "# `env` — a condition relying on the secrets context would silently always be false,",
    "# meaning the tests never run even WITH a key and a green CI would prove nothing.",
    "jobs:",
    "  node:",
    "    runs-on: ubuntu-latest",
    "    env:",
    "      MAILFLAT_API_KEY: ${{ secrets.MAILFLAT_API_KEY }}",
    "    strategy:",
    "      matrix:",
    "        example: [playwright, vitest]",
    "    steps:",
    "      - uses: actions/checkout@v4",
    "      - uses: actions/setup-node@v4",
    "        with: { node-version: 20 }",
    "      - name: Install",
    "        working-directory: ${{ matrix.example }}",
    "        run: npm install",
    "      - name: Smoke test",
    "        if: env.MAILFLAT_API_KEY != \'\'",
    "        working-directory: ${{ matrix.example }}",
    "        run: npm test",
    "",
    "  python:",
    "    runs-on: ubuntu-latest",
    "    env:",
    "      MAILFLAT_API_KEY: ${{ secrets.MAILFLAT_API_KEY }}",
    "    steps:",
    "      - uses: actions/checkout@v4",
    "      - uses: actions/setup-python@v5",
    "        with: { python-version: \"3.11\" }",
    "      - name: Install",
    "        working-directory: pytest",
    "        run: pip install -r requirements.txt",
    "      - name: Smoke test",
    "        if: env.MAILFLAT_API_KEY != \'\'",
    "        working-directory: pytest",
    "        run: pytest",
    "",
  ].join("\n");
}

/**
 * Writes the repo contents under `outDir`.
 *
 * ⚠️ A key is NEVER written: every example ships `.env.example` and no `.env` is produced.
 * (The scaffolder does write `.env`, because there the user is on their own machine; here the
 * repo is public.)
 */
export function buildExamples(outDir) {
  const root = resolve(outDir);
  const dirs = [];
  let count = 0;

  const write = (rel, content) => {
    const full = join(root, rel);
    mkdirSync(dirname(full), { recursive: true });
    writeFileSync(full, content, "utf8");
    count++;
  };

  for (const tpl of TEMPLATES) {
    dirs.push(tpl.id);
    const files = tpl.render({ name: `mailflat-${tpl.id}-example`, key: null });
    for (const [rel, content] of Object.entries(files)) {
      if (rel === ".env") continue; // no .env in a public repo; .env.example is enough
      write(join(tpl.id, rel), content);
    }
  }

  write("README.md", rootReadme());
  write(".github/workflows/examples.yml", ciWorkflow());
  write(".gitignore", ["node_modules/", ".env", ".venv/", "__pycache__/", ""].join("\n"));

  return { root, dirs, files: count };
}

// Generate when run directly.
if (process.argv[1] && process.argv[1].endsWith("build-examples.mjs")) {
  const out = process.argv[2] || new URL("../../../../mailflat-examples", import.meta.url).pathname;
  const res = buildExamples(out);
  process.stdout.write(
    `\n  Wrote ${res.files} files to ${res.root}\n  Examples: ${res.dirs.join(", ")}\n\n`,
  );
}
