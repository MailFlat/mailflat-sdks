// Point this package's `@mailflat/sdk` at the SOURCE next door instead of a registry copy.
//
// Why this exists. `node_modules/@mailflat/sdk` here held **0.6.0** while the source tree
// was on 0.6.2, so every test in this package — and the cross-surface parity matrix — was
// measuring a js-sdk from two releases ago. A fix landing in js-sdk was invisible here
// until someone published it and reinstalled, which is the wrong order: the point of the
// test is to check the change before it ships.
//
// It surfaced when the parity matrix planted an API key in a rejection sentence and found
// it unmasked on the AI SDK column only. The masking was in the js-sdk source; the copy
// being imported predated it. Same class as the stale `dist` this repo already got caught
// by, one level up: a stale DEPENDENCY rather than a stale build.
//
// A symlink rather than a copy, so there is no second artefact to go stale in turn. Run by
// `pretest` and by QA/sdk-parity, and safe to run repeatedly. An `npm install` here will
// replace the link with the registry copy again — that is why it runs on every test, not
// once by hand.
//
// Connected to:
//   - invoked by: package.json `pretest`, QA/sdk-parity/runner_send_result.py
//   - links:      packages/js-sdk (must be built first — `npm --prefix ../js-sdk run build`)

import { existsSync, lstatSync, mkdirSync, rmSync, symlinkSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(here, "../../js-sdk");
const scope = resolve(here, "../node_modules/@mailflat");
const link = join(scope, "sdk");

if (!existsSync(join(target, "dist/index.js"))) {
  console.error(`[use-local-sdk] ${target}/dist is missing — build js-sdk first.`);
  process.exit(1);
}

mkdirSync(scope, { recursive: true });
if (existsSync(link) || lstatSync(link, { throwIfNoEntry: false })) {
  rmSync(link, { recursive: true, force: true });
}
symlinkSync(target, link, "junction");
console.log(`[use-local-sdk] @mailflat/sdk -> ${target}`);
