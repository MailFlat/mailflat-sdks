// Hidden-prompt tests — proof that the key is never echoed (B-053).
//
// What they protect: we stopped recommending `--key` because the key leaks. If the prompt we
// replaced it with also echoed what was typed, we would have gained nothing.
//
// ⚠️ This file LIED once: with a fake `rl`, masking appeared to "pass" while a real terminal
// still printed the key — the first version leaned on readline's private `_writeToOutput`,
// which no longer exists on Node 26. So the tests now drive a REAL readline interface:
// openPrompt gets in-memory streams instead of a tty with `terminal: true`, and the echo
// behaviour travels exactly the same path.
//
// Connected to:
//   - exercises: ../prompt.js (openPrompt, askSecret, ask)
//
// Running it: cd packages/create-mailflat && npm test

import assert from "node:assert/strict";
import { PassThrough } from "node:stream";
import { test } from "node:test";
import { ask, askSecret, openPrompt } from "../prompt.js";

const SECRET = "mf_live_supersecret_1234567890";

/** Real readline plus an in-memory tty stand-in. `terminal: true` turns echo on. */
function harness() {
  const input = new PassThrough();
  const output = new PassThrough();
  const seen = [];
  output.on("data", (c) => seen.push(c.toString("utf8")));
  const rl = openPrompt({ input, output, terminal: true });
  return { rl, input, screen: () => seen.join(""), close: () => rl.close() };
}

/** The user types: send keystrokes a tick later so readline can process the prompt first. */
function types(input, text) {
  setImmediate(() => input.write(`${text}\n`));
}

test("🔒 the typed key never reaches the screen", async () => {
  const { rl, input, screen, close } = harness();
  types(input, SECRET);
  const answer = await askSecret(rl, "  API key?");
  close();

  assert.equal(answer, SECRET, "the answer must still come back intact");
  assert.ok(!screen().includes(SECRET), `the key reached the screen: ${JSON.stringify(screen())}`);
  assert.ok(!screen().includes("supersecret"), "not even part of the key may appear");
});

test("🔒 control: a normal prompt on the same interface DOES echo", async () => {
  // Without this the test above proves nothing — echo could simply have been off already.
  const { rl, input, screen, close } = harness();
  types(input, "visible-answer");
  await ask(rl, "  Project directory?", "mailflat-tests");
  close();
  assert.match(screen(), /visible-answer/, "if echo is off, the masking test passes for free");
});

test("the prompt stays visible and the newline gets through", async () => {
  const { rl, input, screen, close } = harness();
  types(input, SECRET);
  await askSecret(rl, "  API key?");
  close();
  assert.match(screen(), /API key\?/, "the question must be visible or the user is left guessing");
  assert.match(screen(), /\n$/, "the cursor must drop a line after Enter");
});

test("echo comes back AFTER askSecret", async () => {
  const { rl, input, screen, close } = harness();
  types(input, SECRET);
  await askSecret(rl, "  API key?");
  types(input, "after-secret");
  await ask(rl, "  Anything else?", "no");
  close();
  assert.match(screen(), /after-secret/, "later prompts must be visible again");
});

test("an empty answer returns an empty string (skipping the question)", async () => {
  const { rl, input, close } = harness();
  types(input, "");
  assert.equal(await askSecret(rl, "  API key?"), "");
  close();
});

test("the answer is trimmed", async () => {
  const { rl, input, close } = harness();
  types(input, "  mf_live_padded_1234567890  ");
  assert.equal(await askSecret(rl, "  API key?"), "mf_live_padded_1234567890");
  close();
});

test("mute desteklemeyen arabirimde soru yine de sorulur", async () => {
  // Better to ask visibly than to be unable to ask for the key at all.
  const rl = { question: () => Promise.resolve("mf_live_fallback_1234567890 ") };
  assert.equal(await askSecret(rl, "  API key?"), "mf_live_fallback_1234567890");
});
