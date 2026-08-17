// @mailflat/ai-sdk — the MailFlat tool suite for the Vercel AI SDK.
//
// `npm i @mailflat/ai-sdk` → `import { mailflatToolSuite } from "@mailflat/ai-sdk"`.
// `ai` (the Vercel AI SDK) is a peer dependency; the tool objects are spread into
// spread into the `tools` field.
//
// Connected to:
//   - depends on: tools.ts (→ @mailflat/sdk, zod)
//   - used by:    user code (Vercel AI SDK agents)
//
// Key export: mailflatToolSuite (+ tipler)

export { mailflatToolSuite } from "./tools";
export type { ToolSuiteOptions, MailFlatTool } from "./tools";

// package.json have to be same  — `npm test` check this (src/__tests__/version.test.ts).
export const VERSION = "0.8.0";
