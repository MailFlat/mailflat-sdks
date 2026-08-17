// The generated project templates — PURE data plus pure render functions, no disk access.
//
// Each template returns a { "path": "contents" } map from `render(ctx)`; scaffold.js does the
// writing. The split is deliberate: template CORRECTNESS (real SDK names, no leaked key,
// .env in .gitignore) must be testable without touching a filesystem.
//
// ⚠️ The generated code matches the REAL SDK surface — no invented methods:
//   @mailflat/sdk : new MailFlat({apiKey}) · create({prefix,label,retentionHours}) ·
//                   inbox.address · waitForOtp({timeout}) · waitForMessage({timeout}) · delete()
//   mailflat (py) : MailFlat(api_key=) · create(prefix=,label=,retention_hours=) ·
//                   inbox.address · wait_for_otp(timeout=) · wait_for_message(timeout=) · delete()
// `templates.test.mjs` verifies these against the packages' SOURCE files.
//
// Connected to:
//   - imported by: ./scaffold.js, ../../index.js, ./__tests__/*.mjs
//   - depends on:  — (saf)
//
// Key exports:
//   - `TEMPLATES` — [{ id, label, blurb, runner, render(ctx) }]
//   - `TEMPLATE_IDS` · `getTemplate(id)` · `DEFAULT_TEMPLATE`

/** Placeholder written to .env when no key is given — no real key is ever hardcoded. */
const KEY_PLACEHOLDER = "mf_live_your_key_here";

/** Files identical in every project. `.env` stays SECRET; `.env.example` is shared. */
function commonFiles(ctx, { extraIgnores = [], runLines, docsUrl }) {
  return {
    ".gitignore": [".env", "", ...extraIgnores, ""].join("\n"),
    ".env.example": [
      "# Copy to .env and paste your key from https://mailflat.net → Agents → API keys.",
      "# .env is gitignored; this file is not — never put the real key here.",
      `MAILFLAT_API_KEY=${KEY_PLACEHOLDER}`,
      "",
    ].join("\n"),
    ".env": [
      "# Local only — gitignored. Get a key at https://mailflat.net → Agents → API keys.",
      `MAILFLAT_API_KEY=${ctx.key || KEY_PLACEHOLDER}`,
      "",
    ].join("\n"),
    "README.md": [
      `# ${ctx.name}`,
      "",
      `Email tests against a real inbox, using [MailFlat](https://mailflat.net).`,
      "",
      "## Run",
      "",
      "```bash",
      ...runLines,
      "```",
      "",
      ctx.key
        ? "Your API key is already in `.env` (gitignored)."
        : "Put your API key in `.env` first — get one at https://mailflat.net → Agents → API keys.",
      "",
      "## What is in here",
      "",
      "- **The smoke test runs green immediately.** It opens a real inbox, checks the address",
      "  came back, and deletes it — so a failure means the key or the network, not your app.",
      "- **The signup test is the one you edit.** It is skipped until you point `APP_URL` at",
      "  something, because a test that passes without touching your app proves nothing.",
      "",
      "## Notes",
      "",
      "- The address is permanent; only the messages inside expire. `retentionHours` sets that",
      "  window, and your plan caps it.",
      "- An end-to-end encrypted inbox cannot return a one-time code — the server never sees the",
      "  plaintext. Leave test inboxes unencrypted.",
      "",
      `Docs: ${docsUrl}`,
      "",
    ].join("\n"),
  };
}

export const TEMPLATES = [
  // ------------------------------------------------------------------ Playwright
  {
    id: "playwright",
    label: "Playwright",
    blurb: "browser end-to-end: a fixture hands every test its own inbox",
    runner: "npm test",
    render(ctx) {
      return {
        ...commonFiles(ctx, {
          extraIgnores: ["node_modules/", "test-results/", "playwright-report/"],
          runLines: ["npm install", "npx playwright install chromium", "npm test"],
          docsUrl: "https://mailflat.net/docs/automation/playwright",
        }),
        "package.json": JSON.stringify(
          {
            name: ctx.name,
            version: "0.1.0",
            private: true,
            type: "module",
            scripts: { test: "playwright test" },
            devDependencies: {
              "@mailflat/sdk": "^0.7.0",
              "@playwright/test": "^1.48.0",
              dotenv: "^16.4.5",
            },
          },
          null,
          2,
        ) + "\n",
        "playwright.config.ts": [
          "import { defineConfig } from \"@playwright/test\";",
          "import \"dotenv/config\";",
          "",
          "export default defineConfig({",
          "  testDir: \"./e2e\",",
          "  // Workers run in parallel — one inbox per test is what keeps that safe.",
          "  fullyParallel: true,",
          "  use: { baseURL: process.env.APP_URL },",
          "});",
          "",
        ].join("\n"),
        "e2e/fixtures.ts": [
          "// Every test gets its own inbox. Everything after `use` is teardown, so it runs",
          "// even when the test fails.",
          "import { test as base } from \"@playwright/test\";",
          "import { MailFlat, type Inbox } from \"@mailflat/sdk\";",
          "",
          "const mf = new MailFlat({ apiKey: process.env.MAILFLAT_API_KEY! });",
          "",
          "export const test = base.extend<{ inbox: Inbox }>({",
          "  inbox: async ({}, use) => {",
          "    const inbox = await mf.create({ prefix: \"e2e\", retentionHours: 2 });",
          "    await use(inbox);",
          "    await inbox.delete();",
          "  },",
          "});",
          "",
          "export { mf };",
          "export { expect } from \"@playwright/test\";",
          "",
        ].join("\n"),
        "e2e/smoke.spec.ts": [
          "// Proves the key works and cleanup runs. No app required — if this fails, the",
          "// problem is the key or the network, so fix it before reading anything else.",
          "import { test, expect } from \"./fixtures\";",
          "",
          "test(\"the API key opens a real inbox\", async ({ inbox }) => {",
          "  expect(inbox.address).toContain(\"@\");",
          "  expect(await inbox.messages()).toEqual([]);",
          "});",
          "",
        ].join("\n"),
        "e2e/signup.spec.ts": [
          "// The one you edit. Skipped until APP_URL points at something: a signup test that",
          "// passes without touching your app proves nothing.",
          "import { test, expect } from \"./fixtures\";",
          "",
          "test.skip(!process.env.APP_URL, \"set APP_URL in .env to run the signup flow\");",
          "",
          "test(\"a user can sign up with a real one-time code\", async ({ page, inbox }) => {",
          "  await page.goto(\"/signup\");",
          "  await page.fill(\"#email\", inbox.address);",
          "  await page.click(\"#submit\");",
          "",
          "  // Real address, real email, real code — no test-mode backdoor in the app.",
          "  const code = await inbox.waitForOtp({ timeout: 60_000 });",
          "  await page.fill(\"#code\", code);",
          "  await page.click(\"#verify\");",
          "",
          "  await expect(page).toHaveURL(/dashboard/);",
          "});",
          "",
        ].join("\n"),
      };
    },
  },

  // ---------------------------------------------------------------------- Vitest
  {
    id: "vitest",
    label: "Vitest",
    blurb: "no browser: API-level tests for signup, password reset, magic links",
    runner: "npm test",
    render(ctx) {
      return {
        ...commonFiles(ctx, {
          extraIgnores: ["node_modules/", "coverage/"],
          runLines: ["npm install", "npm test"],
          docsUrl: "https://mailflat.net/docs/automation/vitest",
        }),
        "package.json": JSON.stringify(
          {
            name: ctx.name,
            version: "0.1.0",
            private: true,
            type: "module",
            scripts: { test: "vitest run", "test:watch": "vitest" },
            devDependencies: {
              "@mailflat/sdk": "^0.7.0",
              dotenv: "^16.4.5",
              vitest: "^2.1.0",
            },
          },
          null,
          2,
        ) + "\n",
        "vitest.config.ts": [
          "import { defineConfig } from \"vitest/config\";",
          "",
          "export default defineConfig({",
          "  test: {",
          "    setupFiles: [\"dotenv/config\"],",
          "    // Waiting for real mail is slower than a unit test. Say so once, here.",
          "    testTimeout: 90_000,",
          "  },",
          "});",
          "",
        ].join("\n"),
        "tests/mailflat.ts": [
          "// One client for the suite; one inbox per test comes from openInbox().",
          "import { MailFlat, type Inbox } from \"@mailflat/sdk\";",
          "",
          "export const mf = new MailFlat({ apiKey: process.env.MAILFLAT_API_KEY! });",
          "",
          "/** Opens an inbox and registers its cleanup with the caller's afterEach. */",
          "export async function openInbox(label: string): Promise<Inbox> {",
          "  return mf.create({ prefix: \"test\", label, retentionHours: 2 });",
          "}",
          "",
        ].join("\n"),
        "tests/smoke.test.ts": [
          "// Proves the key works and cleanup runs. No app required — if this fails, the",
          "// problem is the key or the network, so fix it before reading anything else.",
          "import { afterEach, expect, test } from \"vitest\";",
          "import type { Inbox } from \"@mailflat/sdk\";",
          "import { openInbox } from \"./mailflat\";",
          "",
          "let inbox: Inbox | null = null;",
          "afterEach(async () => {",
          "  if (inbox) await inbox.delete();",
          "  inbox = null;",
          "});",
          "",
          "test(\"the API key opens a real inbox\", async () => {",
          "  inbox = await openInbox(\"smoke\");",
          "  expect(inbox.address).toContain(\"@\");",
          "  expect(await inbox.messages()).toEqual([]);",
          "});",
          "",
        ].join("\n"),
        "tests/signup.test.ts": [
          "// The one you edit. Skipped until APP_URL points at something: a signup test that",
          "// passes without touching your app proves nothing.",
          "import { afterEach, expect, test } from \"vitest\";",
          "import type { Inbox } from \"@mailflat/sdk\";",
          "import { openInbox } from \"./mailflat\";",
          "",
          "const APP_URL = process.env.APP_URL;",
          "",
          "let inbox: Inbox | null = null;",
          "afterEach(async () => {",
          "  if (inbox) await inbox.delete();",
          "  inbox = null;",
          "});",
          "",
          "test.skipIf(!APP_URL)(\"signup sends a code that arrives\", async () => {",
          "  inbox = await openInbox(\"signup\");",
          "",
          "  await fetch(APP_URL + \"/api/signup\", {",
          "    method: \"POST\",",
          "    headers: { \"content-type\": \"application/json\" },",
          "    body: JSON.stringify({ email: inbox.address }),",
          "  });",
          "",
          "  const code = await inbox.waitForOtp({ timeout: 60_000 });",
          "  expect(code).toMatch(/^[0-9]{4,8}$/);",
          "});",
          "",
        ].join("\n"),
      };
    },
  },

  // ---------------------------------------------------------------------- pytest
  {
    id: "pytest",
    label: "pytest",
    blurb: "Python: a fixture hands every test its own inbox",
    runner: "pytest",
    render(ctx) {
      return {
        ...commonFiles(ctx, {
          extraIgnores: [".venv/", "__pycache__/", ".pytest_cache/"],
          runLines: [
            "python -m venv .venv && source .venv/bin/activate",
            "pip install -r requirements.txt",
            "pytest",
          ],
          docsUrl: "https://mailflat.net/docs/automation/pytest",
        }),
        "requirements.txt": ["mailflat>=0.5.0", "pytest>=8.0", "python-dotenv>=1.0", "requests>=2.31", ""].join("\n"),
        "pytest.ini": ["[pytest]", "testpaths = tests", "addopts = -ra", ""].join("\n"),
        "conftest.py": [
          '"""Shared fixtures: one real inbox per test, deleted afterwards."""',
          "import os",
          "",
          "import pytest",
          "from dotenv import load_dotenv",
          "from mailflat import MailFlat",
          "",
          "load_dotenv()",
          "",
          "",
          "@pytest.fixture(scope=\"session\")",
          "def mf():",
          "    return MailFlat(api_key=os.environ[\"MAILFLAT_API_KEY\"])",
          "",
          "",
          "@pytest.fixture",
          "def inbox(mf):",
          '    """A fresh inbox per test. The yield is the teardown, so it runs on failure too."""',
          "    box = mf.create(prefix=\"test\", retention_hours=2)",
          "    yield box",
          "    box.delete()",
          "",
        ].join("\n"),
        "tests/test_smoke.py": [
          '"""Proves the key works and cleanup runs. No app required — if this fails, the',
          "problem is the key or the network, so fix it before reading anything else.",
          '"""',
          "",
          "",
          "def test_api_key_opens_a_real_inbox(inbox):",
          "    assert \"@\" in inbox.address",
          "    assert inbox.messages() == []",
          "",
        ].join("\n"),
        "tests/test_signup.py": [
          '"""The one you edit. Skipped until APP_URL points at something: a signup test that',
          "passes without touching your app proves nothing.",
          '"""',
          "import os",
          "",
          "import pytest",
          "import requests",
          "",
          "APP_URL = os.getenv(\"APP_URL\")",
          "",
          "",
          "@pytest.mark.skipif(not APP_URL, reason=\"set APP_URL in .env to run the signup flow\")",
          "def test_signup_sends_a_code_that_arrives(inbox):",
          "    requests.post(APP_URL + \"/api/signup\", json={\"email\": inbox.address}, timeout=30)",
          "",
          "    # Real address, real email, real code — no test-mode backdoor in the app.",
          "    code = inbox.wait_for_otp(timeout=60)",
          "    assert code.isdigit()",
          "",
        ].join("\n"),
      };
    },
  },
];

export const TEMPLATE_IDS = TEMPLATES.map((t) => t.id);
export const DEFAULT_TEMPLATE = "playwright";

/** id → template. Null when unknown (the CLI then shows the valid list). */
export function getTemplate(id) {
  return TEMPLATES.find((t) => t.id === id) || null;
}
