// Argument parser tests.
//
// What they protect: every argv shape `npm create` can hand us (npm swallows the `--`
// separator; short and long flags; values with `=` or a space) plus the promise to **never
// throw on any input** — a scaffolder printing a stack trace tells the user nothing.
//
// Connected to:
//   - exercises: ../args.js
//
// Running it: cd packages/create-mailflat && npm test

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { KEY_ENV_VAR, USAGE, keyWarnings, parseArgs, resolveKey, validateDirName } from "../args.js";
import { TEMPLATE_IDS } from "../templates.js";

test("empty argv yields safe defaults", () => {
  const a = parseArgs([]);
  assert.deepEqual(
    { dir: a.dir, template: a.template, key: a.key, yes: a.yes, force: a.force, errors: a.errors },
    { dir: null, template: null, key: null, yes: false, force: false, errors: [] },
  );
});

test("a positional argument becomes the directory", () => {
  assert.equal(parseArgs(["my-tests"]).dir, "my-tests");
});

test("a value is read as both `--t x` and `--t=x`", () => {
  assert.equal(parseArgs(["--template", "pytest"]).template, "pytest");
  assert.equal(parseArgs(["--template=pytest"]).template, "pytest");
  assert.equal(parseArgs(["-t", "pytest"]).template, "pytest");
  assert.equal(parseArgs(["-t=pytest"]).template, "pytest");
});

test("the `--` separator npm leaves behind is ignored", () => {
  // This is how `npm create mailflat@latest my-app -- --template pytest` reaches us.
  const a = parseArgs(["my-app", "--", "--template", "pytest"]);
  assert.equal(a.dir, "my-app");
  assert.equal(a.template, "pytest");
  assert.deepEqual(a.errors, []);
});

test("combined flags are all read", () => {
  const a = parseArgs(["ci-mail", "-t", "vitest", "-k", "mf_live_abc123", "-y", "-f"]);
  assert.equal(a.dir, "ci-mail");
  assert.equal(a.template, "vitest");
  assert.equal(a.key, "mf_live_abc123");
  assert.ok(a.yes && a.force);
});

test("🔒 an unknown template gives a CLEAR error instead of silently defaulting", () => {
  const a = parseArgs(["--template", "jest"]);
  assert.equal(a.errors.length, 1);
  assert.match(a.errors[0], /jest/);
  for (const id of TEMPLATE_IDS) assert.match(a.errors[0], new RegExp(id));
});

test("a flag without a value errors and does not eat the next flag", () => {
  const a = parseArgs(["--template", "--yes"]);
  assert.match(a.errors.join(" "), /--template needs a value/);
  assert.equal(a.template, null);
  assert.ok(a.yes, "--yes must not be swallowed");
});

test("an unknown option errors", () => {
  assert.match(parseArgs(["--turbo"]).errors.join(" "), /unknown option: --turbo/);
});

test("🔒 parseArgs never throws, on any input", () => {
  const nasty = [
    [], ["--"], ["-"], ["--="], ["-t"], ["--key"], ["a", "b", "c"],
    ["--template="], ["", " "], ["-k", "-y"], ["--yes=maybe"],
  ];
  for (const argv of nasty) {
    assert.doesNotThrow(() => parseArgs(argv), `threw on: ${JSON.stringify(argv)}`);
    assert.ok(Array.isArray(parseArgs(argv).errors));
  }
});

test("🔒 a directory name npm would reject is refused HERE", () => {
  // Otherwise the user learns about it at their first `npm install`, inside a generated project.
  assert.match(validateDirName("My-Tests"), /capital letters/);
  assert.match(validateDirName(".hidden"), /starting with/);
  assert.match(validateDirName("_priv"), /starting with/);
  assert.match(validateDirName("has space"), /does not allow/);
  assert.match(validateDirName("a".repeat(215)), /longer than npm allows/);
  assert.match(validateDirName("."), /not a project directory/);
  assert.match(validateDirName(".."), /not a project directory/);
  assert.match(validateDirName("  "), /empty/);
});

test("valid names are accepted (nested paths included)", () => {
  for (const ok of ["mailflat-tests", "e2e", "a.b_c-1", "tmp/nested-project"]) {
    assert.equal(validateDirName(ok), null, `reddedildi: ${ok}`);
  }
});

test("a bad directory name also lands in parseArgs errors", () => {
  assert.match(parseArgs(["My-App"]).errors.join(" "), /capital letters/);
});

test("USAGE lists every template and every flag", () => {
  for (const id of TEMPLATE_IDS) assert.match(USAGE, new RegExp(id));
  for (const flag of ["--template", "--key", "--yes", "--force", "--help", "--version"]) {
    assert.match(USAGE, new RegExp(`\\${flag}`));
  }
});

// ── the key: where it comes from, when we warn (B-053) ───────────────────────

test("no key yields null — the CLI asks, or .env gets a placeholder", () => {
  assert.deepEqual(resolveKey({ flagKey: null, env: {} }), { key: null, source: null });
  assert.deepEqual(resolveKey(), { key: null, source: null });
});

test("🔒 an environment variable can carry the key without a command line", () => {
  // This is the real fix: a way for CI to pass the key without writing `--key`.
  const r = resolveKey({ flagKey: null, env: { [KEY_ENV_VAR]: "mf_live_from_env_1234567890" } });
  assert.deepEqual(r, { key: "mf_live_from_env_1234567890", source: "env" });
});

test("an explicit --key overrides the environment variable", () => {
  const r = resolveKey({ flagKey: "mf_live_flag_1234567890", env: { [KEY_ENV_VAR]: "mf_live_env_1234567890" } });
  assert.deepEqual(r, { key: "mf_live_flag_1234567890", source: "flag" });
});

test("empty or whitespace values do not count as a key", () => {
  assert.equal(resolveKey({ flagKey: "   ", env: { [KEY_ENV_VAR]: "" } }).key, null);
  assert.equal(resolveKey({ flagKey: null, env: { [KEY_ENV_VAR]: "  mf_live_padded_1234567890  " } }).key,
    "mf_live_padded_1234567890");
});

test("🔒 a --key user is told the key LEAKED and must be revoked", () => {
  // Saying "do not do that" is not enough: the moment the command runs, the key is already in
  // the npm log and the shell history, so the user has to revoke it.
  const warnings = keyWarnings("mf_live_1234567890abcdef", "flag").join(" ");
  assert.match(warnings, /revoke/i);
  assert.match(warnings, /_logs|history/i);
  assert.match(warnings, new RegExp(KEY_ENV_VAR));
});

test("no leak warning for a key from the environment or the prompt", () => {
  assert.deepEqual(keyWarnings("mf_live_1234567890abcdef", "env"), []);
  assert.deepEqual(keyWarnings("mf_live_1234567890abcdef", "prompt"), []);
});

test("the format warning works regardless of source", () => {
  assert.match(keyWarnings("sk_test_123", "env").join(" "), /mf_live_/);
  assert.match(keyWarnings("mf_live_x", "prompt").join(" "), /too short/);
  assert.deepEqual(keyWarnings(null, "flag"), [], "no key means no warning");
});

test("🔒 USAGE and the --key help both recommend the environment variable", () => {
  assert.match(USAGE, new RegExp(KEY_ENV_VAR));
  assert.match(USAGE, /DISCOURAGED/);
});

test("🔒 the README never shows a `-k <key>` example", () => {
  // This started as a docs bug: the README recommended `-k mf_live_...`, so keys hit the logs.
  // Even with the code fixed, the bug returns the moment the example does.
  const readme = readFileSync(new URL("../../README.md", import.meta.url), "utf8");
  const examples = readme.match(/^(npm|npx) .*$/gm) ?? [];
  for (const line of examples) {
    assert.doesNotMatch(line, /(^|\s)(-k|--key)(\s|=)/, `README still puts the key on the command line: ${line}`);
  }
  assert.match(readme, new RegExp(KEY_ENV_VAR), "the README must show the recommended path (env var)");
});
