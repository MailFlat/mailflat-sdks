// Argument parser for `npm create mailflat@latest` — a PURE function with no side effects.
//
// Why its own file: this is the one testable piece of the CLI. Writing files (scaffold.js)
// needs a disk and prompting (prompt.js) needs a tty; parsing should need neither.
//
// npm forwards both forms to this file:
//   npm create mailflat@latest my-app -- --template pytest
//   npx create-mailflat my-app --template pytest
// npm swallows the `--` separator itself, so the argv we receive is the same.
//
// Connected to:
//   - imports from: ./templates.js (TEMPLATE_IDS — the single source of valid templates)
//   - imported by:  ../../index.js, ./__tests__/args.test.mjs
//
// Key exports:
//   - `parseArgs(argv)` — { dir, template, key, yes, force, help, version, errors[] }
//   - `resolveKey({ flagKey, env })` — decides where the key comes from
//   - `keyWarnings(key, source)` — leak and format warnings
//   - `KEY_ENV_VAR` · `USAGE`

import { TEMPLATE_IDS } from "./templates.js";

/** The generated `.env` uses the same name, so the user learns one name only. */
export const KEY_ENV_VAR = "MAILFLAT_API_KEY";

export const USAGE = `create-mailflat — scaffold an email-testing project wired to a real inbox

Usage
  npm create mailflat@latest [directory] [options]

Options
  -t, --template <id>   ${TEMPLATE_IDS.join(" | ")}
  -y, --yes             accept defaults, ask nothing (for CI)
  -f, --force           write into a directory that is not empty
  -h, --help            show this
  -v, --version         print the version
  -k, --key <key>       DISCOURAGED — a key on the command line lands in your shell
                        history and in npm's own log file. Set ${KEY_ENV_VAR} instead.

API key
  Read from ${KEY_ENV_VAR} if it is set; otherwise you are asked for it (input is hidden).
  Either way it is written to .env, which is gitignored — never to .env.example.

Examples
  npm create mailflat@latest
  npm create mailflat@latest my-tests -- --template playwright
  # CI: export ${KEY_ENV_VAR} from your secret store, then
  npx create-mailflat ci-mail -t pytest -y`;

const FLAGS = { "-t": "--template", "-k": "--key", "-y": "--yes", "-f": "--force", "-h": "--help", "-v": "--version" };
const TAKES_VALUE = new Set(["--template", "--key"]);

/**
 * Resolve the arguments. NEVER throws — problems come back in `errors[]` and the caller
 * decides. A CLI printing a stack trace tells the user nothing.
 */
export function parseArgs(argv = []) {
  const out = { dir: null, template: null, key: null, yes: false, force: false, help: false, version: false, errors: [] };

  for (let i = 0; i < argv.length; i++) {
    const raw = argv[i];
    if (raw === "--") continue;

    if (!raw.startsWith("-")) {
      if (out.dir === null) out.dir = raw;
      else out.errors.push(`unexpected argument: ${raw}`);
      continue;
    }

    // `--template=x` and `--template x` mean the same thing.
    const eq = raw.indexOf("=");
    const name = FLAGS[raw] || (eq === -1 ? raw : FLAGS[raw.slice(0, eq)] || raw.slice(0, eq));
    let value = eq === -1 ? null : raw.slice(eq + 1);

    if (TAKES_VALUE.has(name)) {
      if (value === null) {
        // PEEK at the next token, do not consume it: with `--template --yes`, `--yes` must
        // survive — the user said two things and only one of them is wrong.
        const next = argv[i + 1];
        if (next !== undefined && !next.startsWith("-")) value = argv[++i];
      }
      if (value === null) {
        out.errors.push(`${name} needs a value`);
        continue;
      }
    }

    switch (name) {
      case "--template": out.template = value; break;
      case "--key": out.key = value; break;
      case "--yes": out.yes = true; break;
      case "--force": out.force = true; break;
      case "--help": out.help = true; break;
      case "--version": out.version = true; break;
      default: out.errors.push(`unknown option: ${raw}`);
    }
  }

  if (out.template !== null && !TEMPLATE_IDS.includes(out.template)) {
    out.errors.push(`unknown template "${out.template}" — pick one of: ${TEMPLATE_IDS.join(", ")}`);
  }
  if (out.dir !== null) {
    const bad = validateDirName(out.dir);
    if (bad) out.errors.push(bad);
  }

  return out;
}

/**
 * Where the key comes from: flag > environment variable > (neither → the CLI asks).
 *
 * The flag wins because it is an explicitly stated intent and ignoring it would be
 * surprising — but the flag path now produces a warning (see keyWarnings).
 *
 * @param {{ flagKey?: string|null, env?: Record<string,string|undefined> }} input
 * @returns {{ key: string|null, source: "flag"|"env"|null }}
 */
export function resolveKey({ flagKey = null, env = {} } = {}) {
  const fromFlag = typeof flagKey === "string" ? flagKey.trim() : "";
  if (fromFlag !== "") return { key: fromFlag, source: "flag" };

  const fromEnv = typeof env[KEY_ENV_VAR] === "string" ? env[KEY_ENV_VAR].trim() : "";
  if (fromEnv !== "") return { key: fromEnv, source: "env" };

  return { key: null, source: null };
}

/**
 * What has to be said about the key.
 *
 * Two separate problems:
 *  - LEAK: a key passed with `--key` survives in the command line npm echoes, in the shell
 *    history and in `~/.npm/_logs/*.log`. A user who does not know this keeps using a leaked
 *    key — which is why the message has to say "revoke it", not just "do not do that".
 *  - FORMAT: catch a mis-pasted key here rather than at the first 401.
 *
 * @param {string|null} key
 * @param {"flag"|"env"|"prompt"|null} source
 * @returns {string[]} warnings to print in order (empty array = nothing wrong)
 */
export function keyWarnings(key, source = null) {
  const out = [];
  if (!key) return out;

  if (source === "flag") {
    out.push(
      `--key put your key on the command line. npm echoes the command and writes it to ` +
        `~/.npm/_logs, and your shell keeps it in history — treat that key as exposed: ` +
        `revoke it at mailflat.net → Agents → API keys, then pass the new one as ${KEY_ENV_VAR}.`,
    );
  }

  if (!key.startsWith("mf_live_")) out.push('that does not look like a MailFlat key (they start with "mf_live_")');
  else if (key.length < 20) out.push("that key looks too short — did it get cut off?");

  return out;
}

/**
 * The directory name is used both as an npm package name (in the generated package.json) and
 * as a path. A name npm would reject is refused HERE, not at the user's first `npm install`.
 *
 * @returns an error message, or null
 */
export function validateDirName(dir) {
  if (dir.trim() === "") return "directory name is empty";
  if (dir.includes("\0")) return "directory name contains a null byte";
  const base = dir.split("/").filter(Boolean).pop() || "";
  if (base === "." || base === "..") return `"${dir}" is not a project directory`;
  if (base.length > 214) return "directory name is longer than npm allows (214)";
  if (base.startsWith(".") || base.startsWith("_")) return `npm rejects package names starting with "${base[0]}"`;
  if (/[A-Z]/.test(base)) return `npm package names cannot contain capital letters — try "${base.toLowerCase()}"`;
  if (!/^[a-z0-9._-]+$/.test(base)) return `"${base}" has characters npm does not allow in a package name`;
  return null;
}
