// Disk-writing tests — against a REAL filesystem, in a temporary directory.
//
// What they protect: the worst thing a scaffolder can do is swallow a file the user wrote. So
// "do not write into a non-empty directory without force" is a contract, not a wish — and the
// tests use a real disk rather than a mock fs (a mock proves nothing about permissions or
// existence).
//
// Connected to:
//   - exercises: ../scaffold.js, ../templates.js
//
// Running it: cd packages/create-mailflat && npm test

import assert from "node:assert/strict";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, test } from "node:test";
import { inspectTarget, scaffold } from "../scaffold.js";
import { TEMPLATE_IDS } from "../templates.js";

const roots = [];
function tmp() {
  const dir = mkdtempSync(join(tmpdir(), "create-mailflat-"));
  roots.push(dir);
  return dir;
}
after(() => {
  for (const r of roots) rmSync(r, { recursive: true, force: true });
});

test("every template is written to the temp directory in full", () => {
  for (const template of TEMPLATE_IDS) {
    const dir = join(tmp(), "proj");
    const res = scaffold({ dir, template, name: "proj" });

    assert.ok(res.files.length >= 7, `${template}: too few files written`);
    assert.deepEqual(res.overwritten, [], `${template}: nothing may be overwritten in an empty dir`);
    for (const rel of res.files) {
      const full = join(dir, rel);
      assert.ok(existsSync(full), `${template}: ${rel} diskte yok`);
      assert.ok(readFileSync(full, "utf8").length > 0, `${template}: ${rel} is empty`);
    }
  }
});

test("nested directories (e2e/, tests/) are created automatically", () => {
  const dir = join(tmp(), "proj");
  const res = scaffold({ dir, template: "playwright", name: "proj" });
  assert.ok(res.files.some((f) => f.includes("/")), "no nested file was produced");
  assert.ok(existsSync(join(dir, "e2e", "fixtures.ts")));
});

test("🔒 a non-empty directory is not written WITHOUT force, and nothing changes", () => {
  const dir = tmp();
  const mine = join(dir, "important.txt");
  writeFileSync(mine, "do not lose me");

  assert.throws(
    () => scaffold({ dir, template: "vitest", name: "proj" }),
    /is not empty/,
  );
  assert.equal(readFileSync(mine, "utf8"), "do not lose me");
  assert.ok(!existsSync(join(dir, "package.json")), "a file was written despite the error");
});

test("force writes, but every overwrite is REPORTED", () => {
  const dir = tmp();
  writeFileSync(join(dir, "README.md"), "mine");
  writeFileSync(join(dir, "notes.txt"), "keep");

  const res = scaffold({ dir, template: "vitest", name: "proj", force: true });

  assert.ok(res.overwritten.includes("README.md"), "the overwrite went unreported");
  assert.ok(!res.overwritten.includes("notes.txt"));
  assert.equal(readFileSync(join(dir, "notes.txt"), "utf8"), "keep", "an unrelated file was destroyed");
  assert.notEqual(readFileSync(join(dir, "README.md"), "utf8"), "mine");
});

test("🔒 on disk the key lives only in .env, never in .env.example", () => {
  const dir = join(tmp(), "proj");
  const KEY = "mf_live_ondiskonly123456";
  const res = scaffold({ dir, template: "pytest", name: "proj", key: KEY });

  assert.match(readFileSync(join(dir, ".env"), "utf8"), new RegExp(KEY));
  for (const rel of res.files) {
    if (rel === ".env") continue;
    assert.ok(!readFileSync(join(dir, rel), "utf8").includes(KEY), `the key leaked into ${rel}`);
  }
});

test("inspectTarget distinguishes missing / empty / non-empty", () => {
  const root = tmp();

  const missing = inspectTarget(join(root, "nope"));
  assert.equal(missing.exists, false);
  assert.equal(missing.empty, true);

  const empty = join(root, "empty");
  mkdirSync(empty);
  assert.deepEqual([inspectTarget(empty).exists, inspectTarget(empty).empty], [true, true]);

  writeFileSync(join(empty, "x"), "");
  assert.equal(inspectTarget(empty).empty, false);
});

test("leftovers like .DS_Store do not make a directory 'non-empty'", () => {
  // Otherwise every folder ever opened in Finder on macOS would demand --force.
  const dir = tmp();
  writeFileSync(join(dir, ".DS_Store"), "");
  assert.equal(inspectTarget(dir).empty, true);
  assert.doesNotThrow(() => scaffold({ dir, template: "vitest", name: "proj" }));
});

test("writing to a target that is a file gives a clear error", () => {
  const root = tmp();
  const file = join(root, "afile");
  writeFileSync(file, "x");
  assert.throws(() => scaffold({ dir: file, template: "vitest", name: "proj" }), /not a directory/);
});

test("an unknown template is rejected before anything is written", () => {
  const dir = join(tmp(), "proj");
  assert.throws(() => scaffold({ dir, template: "jest", name: "proj" }), /unknown template/);
  assert.ok(!existsSync(dir), "a directory was created for a rejected template");
});
