// Template tests — the real question: **does the generated project actually work?**
//
// These are the most valuable tests here, because a scaffolder's mistake explodes inside the
// user's own project at "npm test" time. So templates are verified against the SDKs' SOURCE
// files: does every `inbox.xxx()` call in the generated code really exist?
// (A scar: if the link between docs/templates and code is human memory, there is no link.)
//
// Connected to:
//   - exercises: ../templates.js
//                packages/js-sdk/src/{client,inbox,index}.ts
//                packages/python-sdk/src/mailflat/{client,inbox,__init__}.py
//
// Running it: cd packages/create-mailflat && npm test

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { DEFAULT_TEMPLATE, TEMPLATES, TEMPLATE_IDS, getTemplate } from "../templates.js";

const sdk = (rel) => readFileSync(new URL(`../../../${rel}`, import.meta.url), "utf8");

const CTX = { name: "demo-tests", key: null };
const render = (t) => t.render(CTX);
const allFiles = () => TEMPLATES.flatMap((t) => Object.entries(render(t)).map(([p, c]) => ({ t: t.id, p, c })));
const filesOf = (id, ext) =>
  Object.entries(render(getTemplate(id))).filter(([p]) => p.endsWith(ext));

// ------------------------------------------------------ GROUND TRUTH: the SDK surface

/** The real js-sdk methods (the `async? name(` lines inside class bodies). */
function realJsMethods() {
  const src = sdk("js-sdk/src/client.ts") + sdk("js-sdk/src/inbox.ts");
  return new Set([...src.matchAll(/^ {2}(?:async )?([a-zA-Z][\w]*)\(/gm)].map((m) => m[1]));
}

/** Names js-sdk exports from `index.ts` (everything that can be imported). */
function realJsExports() {
  const src = sdk("js-sdk/src/index.ts");
  const names = new Set();
  for (const m of src.matchAll(/export (?:type )?\{([^}]+)\}/g)) {
    for (const part of m[1].split(",")) {
      const name = part.trim().split(/\s+as\s+/).pop().trim();
      if (name) names.add(name);
    }
  }
  return names;
}

/** The real python-sdk methods. */
function realPyMethods() {
  const src = sdk("python-sdk/src/mailflat/client.py") + sdk("python-sdk/src/mailflat/inbox.py");
  return new Set([...src.matchAll(/^ {4}def ([a-z_][\w]*)\(/gm)].map((m) => m[1]));
}

/** Names available via `from mailflat import X`. */
function realPyExports() {
  const src = sdk("python-sdk/src/mailflat/__init__.py");
  const block = src.split("__all__")[1]?.split("]")[0] || "";
  return new Set([...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]));
}

test("🔒 every MailFlat method in the generated TS code REALLY exists in the SDK", () => {
  const real = realJsMethods();
  assert.ok(real.has("waitForOtp"), "could not read js-sdk — has the regex gone stale?");

  for (const id of ["playwright", "vitest"]) {
    for (const [path, code] of filesOf(id, ".ts")) {
      for (const m of code.matchAll(/\b(?:inbox|box|mf)\.([a-zA-Z]\w*)\(/g)) {
        assert.ok(real.has(m[1]), `${id}/${path}: @mailflat/sdk has no "${m[1]}()"`);
      }
    }
  }
});

test("🔒 the generated TS code only imports names `@mailflat/sdk` really exports", () => {
  const exported = realJsExports();
  assert.ok(exported.has("MailFlat"), "could not read index.ts — has the regex gone stale?");

  for (const id of ["playwright", "vitest"]) {
    for (const [path, code] of filesOf(id, ".ts")) {
      for (const imp of code.matchAll(/import (?:type )?\{([^}]+)\} from "@mailflat\/sdk"/g)) {
        for (const part of imp[1].split(",")) {
          const name = part.replace(/^\s*type\s+/, "").trim();
          if (name) assert.ok(exported.has(name), `${id}/${path}: @mailflat/sdk "${name}" vermiyor`);
        }
      }
    }
  }
});

test("🔒 every MailFlat method in the generated Python code REALLY exists in the SDK", () => {
  const real = realPyMethods();
  assert.ok(real.has("wait_for_otp"), "could not read python-sdk — has the regex gone stale?");

  for (const [path, code] of Object.entries(render(getTemplate("pytest")))) {
    if (!path.endsWith(".py")) continue;
    for (const m of code.matchAll(/\b(?:inbox|box|mf)\.([a-z_]\w*)\(/g)) {
      assert.ok(real.has(m[1]), `pytest/${path}: the mailflat SDK has no "${m[1]}()"`);
    }
  }
});

test("🔒 the generated Python `from mailflat import` names are real", () => {
  const exported = realPyExports();
  assert.ok(exported.has("MailFlat"), "could not read __init__.py — has the regex gone stale?");

  for (const [path, code] of Object.entries(render(getTemplate("pytest")))) {
    if (!path.endsWith(".py")) continue;
    for (const imp of code.matchAll(/from mailflat import ([\w, ]+)/g)) {
      for (const name of imp[1].split(",").map((s) => s.trim())) {
        if (name) assert.ok(exported.has(name), `pytest/${path}: mailflat "${name}" vermiyor`);
      }
    }
  }
});

test("🔒 create() option names match what the SDK accepts", () => {
  // An invented option is silently ignored: a Python template writing `retentionHours`
  // instead of `retention_hours` raises nothing, retention simply never applies.
  const tsOpts = new Set([...sdk("js-sdk/src/types.ts").matchAll(/^ {2}(\w+)\??:/gm)].map((m) => m[1]));
  for (const id of ["playwright", "vitest"]) {
    for (const [path, code] of filesOf(id, ".ts")) {
      for (const call of code.matchAll(/\.create\(\{([^}]*)\}/g)) {
        for (const key of call[1].matchAll(/(\w+):/g)) {
          assert.ok(tsOpts.has(key[1]), `${id}/${path}: create() does not know the "${key[1]}" option`);
        }
      }
    }
  }

  const pySig = sdk("python-sdk/src/mailflat/client.py").match(/def create\(([\s\S]*?)\)\s*->/)?.[1] || "";
  const pyOpts = new Set([...pySig.matchAll(/(\w+)\s*[:=]/g)].map((m) => m[1]));
  assert.ok(pyOpts.has("retention_hours"), "could not read the create() signature in client.py");
  for (const [path, code] of Object.entries(render(getTemplate("pytest")))) {
    if (!path.endsWith(".py")) continue;
    for (const call of code.matchAll(/\.create\(([^)]*)\)/g)) {
      for (const key of call[1].matchAll(/(\w+)=/g)) {
        assert.ok(pyOpts.has(key[1]), `pytest/${path}: create() does not know the "${key[1]}" option`);
      }
    }
  }
});

// ------------------------------------------------------------- security

test("🔒 the key is written ONLY to .env, never to .env.example", () => {
  const KEY = "mf_live_realkey1234567890";
  for (const t of TEMPLATES) {
    const files = t.render({ name: "x", key: KEY });
    assert.match(files[".env"], new RegExp(KEY), `${t.id}: .env did not receive the key`);
    for (const [path, content] of Object.entries(files)) {
      if (path === ".env") continue;
      assert.ok(!content.includes(KEY), `${t.id}: the key leaked into ${path}`);
    }
  }
});

test("🔒 every template's .gitignore covers .env", () => {
  for (const t of TEMPLATES) {
    const lines = t.render(CTX)[".gitignore"].split("\n").map((l) => l.trim());
    assert.ok(lines.includes(".env"), `${t.id}: .gitignore misses .env — the key would be committed`);
  }
});

test("🔒 with no key given, no file contains anything that looks like a real key", () => {
  for (const { t, p, c } of allFiles()) {
    // `_` is part of the class so the placeholder (`mf_live_your_key_here`) matches whole;
    // otherwise the fragment "mf_live_your" looks like a real key.
    for (const k of c.match(/mf_live_[A-Za-z0-9_]+/g) || []) {
      assert.ok(
        k === "mf_live_your_key_here",
        `${t}/${p}: real-looking key "${k}"`,
      );
    }
  }
});

// --------------------------------------------------------------- content

test("every template produces the same base files", () => {
  for (const t of TEMPLATES) {
    const files = render(t);
    for (const required of [".env", ".env.example", ".gitignore", "README.md"]) {
      assert.ok(files[required], `${t.id}: ${required} yok`);
    }
    assert.ok(Object.keys(files).length >= 7, `${t.id}: too few files`);
  }
});

test("🔒 no generated file is empty and no placeholder survives", () => {
  for (const { t, p, c } of allFiles()) {
    assert.ok(c.trim().length > 0, `${t}/${p} is empty`);
    assert.ok(!/\{\{|\}\}|undefined|\[object Object\]/.test(c), `${t}/${p}: unrendered placeholder left`);
    assert.ok(c.endsWith("\n"), `${t}/${p}: file does not end with a newline`);
  }
});

test("generated package.json files are valid JSON and carry the project name", () => {
  for (const id of ["playwright", "vitest"]) {
    const raw = render(getTemplate(id))["package.json"];
    const parsed = JSON.parse(raw);
    assert.equal(parsed.name, CTX.name);
    assert.ok(parsed.private, `${id}: a generated project must be private (no accidental publish)`);
    assert.ok(parsed.devDependencies["@mailflat/sdk"], `${id}: the SDK dependency is missing`);
    assert.ok(parsed.scripts.test, `${id}: no test script`);
  }
});

test("🔒 every template ships a working smoke test AND a flow test to adapt", () => {
  // Shipping only self-green tests makes the scaffolder useless; shipping only tests that
  // need a real app turns the first run red. Both are required.
  for (const t of TEMPLATES) {
    const paths = Object.keys(render(t));
    assert.ok(paths.some((p) => /smoke/.test(p)), `${t.id}: smoke testi yok`);
    assert.ok(paths.some((p) => /signup/.test(p)), `${t.id}: signup testi yok`);
  }
});

test("🔒 the flow test SKIPS without APP_URL (we do not ship a false green)", () => {
  for (const t of TEMPLATES) {
    const files = render(t);
    const signup = Object.entries(files).find(([p]) => /signup/.test(p))[1];
    assert.match(signup, /APP_URL/, `${t.id}: the signup test does not look at APP_URL`);
    assert.match(signup, /skip/i, `${t.id}: the signup test does not skip without APP_URL → false green`);
  }
});

test("🔒 every template demonstrates cleanup (we do not teach leaking inboxes)", () => {
  for (const t of TEMPLATES) {
    const code = Object.entries(render(t))
      .filter(([p]) => /\.(ts|py)$/.test(p))
      .map(([, c]) => c)
      .join("\n");
    assert.match(code, /\.delete\(\)/, `${t.id}: no inbox is ever deleted`);
    assert.match(code, /retention_?[hH]ours/, `${t.id}: retention is never mentioned`);
  }
});

test("each template README shows its own run commands", () => {
  for (const t of TEMPLATES) {
    const readme = render(t)["README.md"];
    assert.match(readme, new RegExp(CTX.name), `${t.id}: the README does not carry the project name`);
    assert.match(readme, /mailflat\.net\/docs/, `${t.id}: the README has no docs link`);
    assert.ok(readme.includes(t.runner.split(" ")[0]), `${t.id}: the README does not show the run command`);
  }
});

test("the README says so when a key was given, and warns when it was not", () => {
  const withKey = getTemplate("vitest").render({ name: "x", key: "mf_live_abc" })["README.md"];
  const without = getTemplate("vitest").render({ name: "x", key: null })["README.md"];
  assert.match(withKey, /already in `\.env`/);
  assert.match(without, /Put your API key/);
});

test("template ids are unique and the default points at a real template", () => {
  assert.equal(new Set(TEMPLATE_IDS).size, TEMPLATE_IDS.length);
  assert.ok(getTemplate(DEFAULT_TEMPLATE), "DEFAULT_TEMPLATE matches no template");
  assert.equal(getTemplate("jest"), null);
});

// ==================================================== dependency pin (drift)
/**
 * 🔒 The range the template pins must ADMIT the published SDK.
 *
 * Real case (8 Aug 2026, found by an outside agent using MailFlat): `templates.js` said
 * `"@mailflat/sdk": "^0.3.2"` in two places. In npm, a caret on a 0.x version locks the
 * MINOR, so `^0.3.2` resolves to 0.3.2 alone — 0.4.0 and 0.5.0 fall outside it. A user who
 * ran `npm create mailflat` and then wrote the JS example from the docs hit
 * `waitUntilSent is not a function`: cc, bcc, attachments, waitUntilSent, reply and
 * inReplyTo are all absent from 0.3.2 and all present in 0.5.0.
 *
 * Why nobody noticed: the scaffold still generated, every file was in place, and the tests
 * stayed green. Nothing looked at whether the generated package.json resolved to a version
 * that actually carries the API the documentation promises. A file existing is not the same
 * as a file working.
 */
test("🔒 the template's @mailflat/sdk pin admits the published SDK", () => {
  const src = readFileSync(new URL("../templates.js", import.meta.url), "utf8");
  const pins = [...src.matchAll(/"@mailflat\/sdk":\s*"\^(\d+)\.(\d+)\.(\d+)"/g)]
    .map((m) => m.slice(1, 4).map(Number));
  assert.ok(pins.length > 0, "no @mailflat/sdk pin found in templates.js — has the regex gone stale?");

  const sdkVer = JSON.parse(sdk("js-sdk/package.json")).version;
  const [sMaj, sMin, sPatch] = sdkVer.split(".").map(Number);

  for (const [maj, min, patch] of pins) {
    const pin = `^${maj}.${min}.${patch}`;
    assert.equal(maj, sMaj, `${pin} has a different major than the published SDK ${sdkVer}`);
    if (sMaj === 0) {
      // On 0.x a caret locks the MINOR: ^0.3.2 means >=0.3.2 <0.4.0. If the minor differs,
      // the published version is outside the range and the user gets an older API.
      assert.equal(min, sMin,
        `${pin} resolves to 0.${min}.x only, but the published SDK is ${sdkVer} — ` +
        `the user would get ${sMin > min ? "an older" : "a nonexistent"} API. Pin ^${sdkVer}.`);
    }
    assert.ok(patch <= sPatch, `${pin} is ahead of the published SDK ${sdkVer}`);
  }
});

/**
 * 🔒 The generated JS must call methods the pinned SDK actually has.
 *
 * The test above compares versions; this one compares surfaces. Either can be wrong on its
 * own: the right version can be pinned while a nonexistent method is called, or the reverse.
 */
test("🔒 every SDK method the template calls exists in the js-sdk source", () => {
  const surface = sdk("js-sdk/src/inbox.ts") + sdk("js-sdk/src/client.ts");
  const generated = TEMPLATE_IDS
    .filter((id) => id !== "pytest")
    .flatMap((id) => Object.values(render(getTemplate(id))))
    .join("\n");

  const called = new Set([...generated.matchAll(/\b(?:inbox|mf|box)\.(\w+)\(/g)].map((m) => m[1]));
  assert.ok(called.size > 0, "no SDK calls found in the generated code — has the regex gone stale?");
  for (const name of called) {
    assert.match(surface, new RegExp(`\\b(async\\s+)?${name}\\s*\\(`),
      `the template calls inbox.${name}() but the js-sdk source has no such method`);
  }
});
