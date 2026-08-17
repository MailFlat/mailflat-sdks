// Does the published version match what the package reports — guards against B-052.
//
// Same rationale as js-sdk/src/__tests__/version.test.ts: `VERSION` is a literal baked into
// the bundle, so comparing it to package.json is the only guard.
//
// Connected to:
//   - exercises: ../index.ts (VERSION) ↔ ../../package.json (version)
//
// Running it: cd packages/ai-sdk && npm test

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { VERSION } from "../index";

const pkg = JSON.parse(readFileSync(new URL("../../package.json", import.meta.url), "utf8"));

describe("VERSION", () => {
  it("🔒 is exactly the version in package.json", () => {
    expect(VERSION).toBe(pkg.version);
  });

  it("looks like a release number", () => {
    expect(VERSION).toMatch(/^\d+\.\d+\.\d+(-.+)?$/);
  });
});

describe("the @mailflat/sdk dependency pin", () => {
  // Mirrors create-mailflat's lock, which earned its place: a stale `^0.3.2` pin sat in the
  // scaffold for three SDK releases and shipped `waitUntilSent is not a function` to anyone
  // who followed the docs. On 0.x a caret locks the MINOR — `^0.5.0` means >=0.5.0 <0.6.0 —
  // so a pin one minor behind silently installs an SDK without the current fixes.
  //
  // ai-sdk had no such lock and hit exactly that on day 68: the published ai-sdk@0.6.0
  // depends on @mailflat/sdk ^0.5.0, so every install of it resolves to an SDK still
  // carrying B-077 (unknown fields dropped) and B-079 (account key printed). The release
  // verifier said ✓ because it only asks whether an upper bound EXISTS, not whether the
  // range admits the sibling we just published.
  const sdkPkg = JSON.parse(
    readFileSync(new URL("../../../js-sdk/package.json", import.meta.url), "utf8"),
  );

  it("🔒 admits the @mailflat/sdk version in this repo", () => {
    const pin = pkg.dependencies?.["@mailflat/sdk"];
    expect(pin, "@mailflat/sdk is not in dependencies — has the package been restructured?")
      .toBeTruthy();

    const match = /^\^(\d+)\.(\d+)\.(\d+)$/.exec(pin);
    expect(match, `unexpected pin shape ${pin} — this lock only understands caret pins`)
      .not.toBeNull();

    const [major, minor, patch] = match!.slice(1, 4).map(Number);
    const [sdkMajor, sdkMinor, sdkPatch] = String(sdkPkg.version).split(".").map(Number);

    expect(major, `${pin} has a different major than the SDK ${sdkPkg.version}`).toBe(sdkMajor);
    if (sdkMajor === 0) {
      expect(
        minor,
        `${pin} resolves to 0.${minor}.x only, but the SDK is ${sdkPkg.version} — ` +
          `installing this package would pull an older SDK. Pin ^${sdkPkg.version}.`,
      ).toBe(sdkMinor);
    }
    expect(patch, `${pin} is ahead of the SDK ${sdkPkg.version}`).toBeLessThanOrEqual(sdkPatch);
  });

  it("🔒 the tests actually import the sibling, not a registry copy", () => {
    // The lock above checks what package.json DECLARES. It was green the whole time
    // node_modules/@mailflat/sdk held **0.6.0** while the source next door was on 0.6.2 —
    // so every test in this package, and the cross-surface parity matrix, measured an SDK
    // from two releases back. A fix landing in js-sdk was invisible here until someone
    // published it and reinstalled, which is the wrong order: the test exists to check the
    // change BEFORE it ships. It surfaced when the parity matrix planted an API key in a
    // rejection sentence and found it unmasked on this surface only, while the masking sat
    // in the js-sdk source. Declared version and resolved version are different claims;
    // this is the second one. `pretest` runs scripts/use-local-sdk.mjs to keep it true.
    const resolved = JSON.parse(
      readFileSync(new URL("../../node_modules/@mailflat/sdk/package.json", import.meta.url),
        "utf8"),
    );
    expect(
      resolved.version,
      `node_modules/@mailflat/sdk is ${resolved.version} but the source is ` +
        `${sdkPkg.version} — this suite is measuring a stale copy. ` +
        `Run: node ./scripts/use-local-sdk.mjs`,
    ).toBe(sdkPkg.version);
  });
});
