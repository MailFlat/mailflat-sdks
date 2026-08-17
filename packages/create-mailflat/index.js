#!/usr/bin/env node
// `npm create mailflat@latest` entry point — asks the questions, writes the template to disk.
//
// The only job here is flow control: parsing lives in args.js, content in templates.js,
// writing in scaffold.js, questions in prompt.js. This file is not tested; the parts that are
// tested were split out so they need neither a filesystem nor a tty.
//
// Connected to:
//   - imports from: src/args.js, src/templates.js, src/scaffold.js, src/prompt.js
//   - imported by:  — (bin entry point)
//
// Key export: — (yan etkili script)

import { readFileSync } from "node:fs";
import process from "node:process";

import { KEY_ENV_VAR, USAGE, keyWarnings, parseArgs, resolveKey, validateDirName } from "./src/args.js";
import { DEFAULT_TEMPLATE, TEMPLATES, getTemplate } from "./src/templates.js";
import { inspectTarget, scaffold } from "./src/scaffold.js";
import { ask, askSecret, choose, isInteractive, openPrompt } from "./src/prompt.js";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

const say = (line = "") => process.stdout.write(`${line}\n`);
const die = (line) => {
  process.stderr.write(`\n  ${line}\n\n`);
  process.exit(1);
};

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (args.help) return say(USAGE);
  if (args.version) return say(pkg.version);
  if (args.errors.length) die(args.errors.join("\n  "));

  say(`\n  create-mailflat — a test project wired to a real inbox\n`);

  const interactive = isInteractive() && !args.yes;
  const rl = interactive ? openPrompt() : null;

  try {
    // 1) directory
    let dir = args.dir;
    if (!dir) {
      dir = interactive ? await ask(rl, "  Project directory?", "mailflat-tests") : "mailflat-tests";
      const bad = validateDirName(dir);
      if (bad) die(bad);
    }

    // 2) template
    let templateId = args.template;
    if (!templateId) {
      templateId = interactive
        ? (await choose(rl, "\n  Which runner?", TEMPLATES)).id
        : DEFAULT_TEMPLATE;
    }
    const tpl = getTemplate(templateId);

    // 3) key — flag > MAILFLAT_API_KEY > prompt. Skipping is fine: .env gets a placeholder.
    let { key, source } = resolveKey({ flagKey: args.key, env: process.env });
    if (key === null && interactive) {
      say(`\n  Your API key goes in .env (gitignored). Get one at mailflat.net → Agents → API keys.`);
      say(`  (Nothing is echoed as you type. In CI, set ${KEY_ENV_VAR} instead.)`);
      const typed = await askSecret(rl, "  API key? [enter to skip]");
      key = typed === "" ? null : typed;
      source = key ? "prompt" : null;
    }
    if (source === "env") say(`\n  Using the key from ${KEY_ENV_VAR}.`);
    for (const warn of keyWarnings(key, source)) say(`\n  ⚠ ${warn}`);

    // 4) write — ask before touching a non-empty directory, never overwrite silently
    const target = inspectTarget(dir);
    let force = args.force;
    if (!target.empty && !force) {
      if (!interactive) {
        die(`${target.path} is not empty (${target.entries.length} entries) — pass --force to write anyway`);
      }
      const answer = await ask(rl, `\n  ${target.path} already has files. Write into it anyway? [y/N]`, "N");
      if (!/^y(es)?$/i.test(answer)) die("nothing written");
      force = true;
    }

    const name = dir.split("/").filter(Boolean).pop();
    const result = scaffold({ dir, template: templateId, name, key, force });

    say(`\n  Created ${result.files.length} files in ${result.path}\n`);
    for (const f of result.files) say(`    ${f}${result.overwritten.includes(f) ? "  (overwritten)" : ""}`);

    say(`\n  Next:\n`);
    say(`    cd ${dir}`);
    if (!key) say(`    # put your key in .env first`);
    for (const line of tpl.render({ name, key })["README.md"].split("\n")) {
      // Read the run commands from the README instead of repeating them here: one source.
      if (line.startsWith("npm ") || line.startsWith("npx ") || line.startsWith("pip ") ||
          line.startsWith("pytest") || line.startsWith("python ")) {
        say(`    ${line}`);
      }
    }
    say(`\n  The smoke test runs green with just a key. The signup test is the one you edit.\n`);
  } finally {
    rl?.close();
  }
}

main().catch((err) => die(err?.message || String(err)));
