// Vitest config — tests resolve `@mailflat/sdk` from LOCAL SOURCE.
//
// Why this is needed: this monorepo does not use npm workspaces, so `node_modules/@mailflat/sdk`
// is a COPY of the published version. Without the alias the tests run against whatever is on
// npm today; when a new export lands (e.g. `redactSecrets`, B-055) they fail with "is not a
// function" and the actual change is never exercised.
//
// The alias is test-only: the build (tsup) and the release use the real dependency, which is
// why the range in package.json must REQUIRE a version containing the export in use.
//
// Connected to:
//   - used by:    vitest (npm test)
//   - depends on: ../js-sdk/src/index.ts

import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@mailflat/sdk": fileURLToPath(new URL("../js-sdk/src/index.ts", import.meta.url)),
    },
  },
});
