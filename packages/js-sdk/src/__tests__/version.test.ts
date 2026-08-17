// Does the published version match what the package reports — guards against B-052.
//
// What it protects: npm had 0.2.0 while `VERSION` said "0.1.0". `VERSION` is a literal baked
// into the bundle, so the only real guard is this comparison against package.json — plus
// package.json running the tests in `prepublishOnly`.
//
// Connected to:
//   - exercises: ../index.ts (VERSION) ↔ ../../package.json (version)
//
// Running it: cd packages/js-sdk && npm test

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

describe("public export surface", () => {
  // Probing the published package showed `MAX_SUBJECT_CHARS` was missing from the package
  // root: the tests imported it from "../types" directly, so they proved the value existed
  // without proving a USER could reach it. Importing through the entry point is the only
  // way to test what ships.
  it("🔒 exports the subject limit next to the helper that enforces it", async () => {
    const pkg = await import("../index");
    expect(typeof (pkg as any).replySubject).toBe("function");
    expect(typeof (pkg as any).MAX_SUBJECT_CHARS).toBe("number");
  });
});
