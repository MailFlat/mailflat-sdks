// The package's single version constant.
//
// Why its own file: `client.ts` reads it for the User-Agent and `index.ts` re-exports it.
// It used to live only in index.ts, where reading it from client.ts meant either a circular
// import or a `require()` that breaks in an ESM bundle. A separate leaf module solves both.
//
// package.json ile AYNI olmak zorunda — `npm test` bunu kontrol eder
// (src/__tests__/version.test.ts), and `prepublishOnly` runs the tests.
// Releasing = bump both together.
// Why it is not imported from package.json: this string is baked into the bundle, and a JSON
// import makes
// both the dual CJS/ESM output and the d.ts generation fragile.
//
// Connected to:
//   - used by: index.ts (re-export), client.ts (User-Agent)
//
// Key export: VERSION

export const VERSION = "0.7.0";
