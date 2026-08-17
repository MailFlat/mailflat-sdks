// Tests for the `mailflat-examples` generator.
//
// What they protect: because this writes to a PUBLIC repo, the critical claim is that **no key
// leaks** — the scaffolder writes `.env` (on the user's own machine) but the generator must
// not (the repo is public). Second: the repo must be the OUTPUT of the templates; a hand-added
// example drifts silently.
//
// Connected to:
//   - exercises: ../../scripts/build-examples.mjs, ../templates.js
//
// Running it: cd packages/create-mailflat && npm test

import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import { buildExamples } from "../../scripts/build-examples.mjs";
import { TEMPLATES } from "../templates.js";

const roots = [];
function build() {
  const dir = mkdtempSync(join(tmpdir(), "mailflat-examples-"));
  roots.push(dir);
  return { dir, res: buildExamples(dir) };
}
after(() => {
  for (const r of roots) rmSync(r, { recursive: true, force: true });
});

test("a directory is generated for every template", () => {
  const { dir, res } = build();
  assert.deepEqual(res.dirs.sort(), TEMPLATES.map((t) => t.id).sort());
  for (const id of res.dirs) {
    assert.ok(existsSync(join(dir, id)), `${id}/ is missing`);
    assert.ok(readdirSync(join(dir, id)).length >= 5, `${id}/ is suspiciously empty`);
  }
});

test("🔒 no .env is written to the public repo — only .env.example", () => {
  // The scaffolder writes .env on the user's own machine. Here the repo is public: even an
  // empty .env is an invitation to "put your key here and commit it".
  const { dir, res } = build();
  for (const id of res.dirs) {
    assert.ok(!existsSync(join(dir, id, ".env")), `${id}/.env was written to the public repo`);
    assert.ok(existsSync(join(dir, id, ".env.example")), `${id}/.env.example yok`);
  }
});

test("🔒 no generated file contains anything resembling a key", () => {
  const { dir } = build();
  const walk = (p) => readdirSync(p, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(p, e.name)) : [join(p, e.name)]);

  for (const file of walk(dir)) {
    for (const k of readFileSync(file, "utf8").match(/mf_live_[A-Za-z0-9_]+/g) || []) {
      assert.equal(k, "mf_live_your_key_here", `${file}: real-looking key "${k}"`);
    }
  }
});

test("the root README links every example and the docs site", () => {
  const { dir } = build();
  const readme = readFileSync(join(dir, "README.md"), "utf8");
  for (const t of TEMPLATES) {
    assert.match(readme, new RegExp(`\\./${t.id}`), `the README does not link ${t.id}/`);
    assert.match(readme, new RegExp(t.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(readme, /mailflat\.net\/docs\/automation/, "no docs link for the remaining runners");
  assert.match(readme, /create-mailflat/, "the README does not say it is generated → someone will edit it");
});

test("🔒 the README says EXPLICITLY that it must not be edited by hand", () => {
  // Hand-editing a generated repo is the easiest way to bring drift back.
  const { dir } = build();
  assert.match(readFileSync(join(dir, "README.md"), "utf8"), /Generated, not hand-written/);
});

test("the CI workflow runs every example and SKIPS when there is no key", () => {
  const { dir } = build();
  const ci = readFileSync(join(dir, ".github/workflows/examples.yml"), "utf8");
  for (const t of TEMPLATES) assert.match(ci, new RegExp(t.id), `CI does not run the ${t.id} example`);
  // A fork has no key; the job must skip rather than go red.
  assert.match(ci, /if: env\.MAILFLAT_API_KEY != ''/, "the smoke step does not skip without a key");
  // 🔒 The `secrets` context is NOT available in a step-level `if:` — using it makes the
  // condition always false, so the tests silently never run even WITH a key. A "green CI"
  // would then prove nothing.
  assert.ok(
    !/if:.*secrets\./.test(ci),
    "secrets used in a step `if:` — the condition silently stays false",
  );
  assert.match(ci, /env:\n\s+MAILFLAT_API_KEY: \$\{\{ secrets\.MAILFLAT_API_KEY \}\}/,
    "the secret is not lifted into env at job level");
  assert.match(ci, /schedule:/, "no weekly run — SDK drift must be caught before a user hits it");
});

test("the root .gitignore covers node_modules and .env", () => {
  const { dir } = build();
  const lines = readFileSync(join(dir, ".gitignore"), "utf8").split("\n").map((l) => l.trim());
  for (const must of ["node_modules/", ".env"]) {
    assert.ok(lines.includes(must), `.gitignore is missing "${must}"`);
  }
});

test("generating twice yields the same result (deterministic)", () => {
  const a = build();
  const b = build();
  const read = ({ dir }, rel) => readFileSync(join(dir, rel), "utf8");
  assert.equal(read(a, "README.md"), read(b, "README.md"));
  assert.equal(read(a, "pytest/conftest.py"), read(b, "pytest/conftest.py"));
});
