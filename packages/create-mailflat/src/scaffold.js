// The layer that writes template output to disk — the only file with side effects.
//
// The rule: never overwrite silently. Writing into a non-empty directory requires `force`,
// and even then the changed files are reported back — because the worst thing a scaffolder can
// do is swallow a file the user wrote.
//
// Connected to:
//   - imports from: node:fs, node:path, ./templates.js
//   - imported by:  ../../index.js, ./__tests__/scaffold.test.mjs
//
// Key exports:
//   - `inspectTarget(dir)` — { exists, empty, entries[] }
//   - `scaffold({ dir, template, name, key, force })` — { files[], overwritten[] }

import { existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { getTemplate } from "./templates.js";

/** Leftovers like `.DS_Store` do not count as "non-empty" — otherwise macOS blocks everything. */
const IGNORABLE = new Set([".DS_Store", "Thumbs.db", ".git"]);

/** State of the target directory. Asked BEFORE writing; the CLI makes the call. */
export function inspectTarget(dir) {
  const path = resolve(dir);
  if (!existsSync(path)) return { path, exists: false, empty: true, entries: [] };
  if (!statSync(path).isDirectory()) return { path, exists: true, empty: false, entries: [], notADirectory: true };
  const entries = readdirSync(path).filter((e) => !IGNORABLE.has(e));
  return { path, exists: true, empty: entries.length === 0, entries };
}

/**
 * Projeyi yaz.
 *
 * @returns { files, overwritten } — both are sorted paths relative to the directory
 * @throws  when the target is non-empty and force was not given (the CLI turns this into a message)
 */
export function scaffold({ dir, template, name, key = null, force = false }) {
  const tpl = getTemplate(template);
  if (!tpl) throw new Error(`unknown template: ${template}`);

  const target = inspectTarget(dir);
  if (target.notADirectory) throw new Error(`${target.path} exists and is not a directory`);
  if (!target.empty && !force) {
    throw new Error(`${target.path} is not empty (${target.entries.length} entries) — pass --force to write anyway`);
  }

  const files = tpl.render({ name, key });
  const written = [];
  const overwritten = [];

  for (const rel of Object.keys(files).sort()) {
    const full = join(target.path, rel);
    mkdirSync(dirname(full), { recursive: true });
    if (existsSync(full)) overwritten.push(rel);
    writeFileSync(full, files[rel], "utf8");
    written.push(rel);
  }

  return { files: written, overwritten, path: target.path };
}
