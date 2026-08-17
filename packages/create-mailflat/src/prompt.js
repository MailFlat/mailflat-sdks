// Terminal prompts — dependency-free (readline/promises), self-disabling without a tty.
//
// Why no prompt library: this package is downloaded and executed on the spot by
// `npm create`, so every dependency costs both download time and supply-chain surface (a
// deliberate choice after the RCE incident). All we need is to ask two questions.
//
// Connected to:
//   - imports from: node:readline/promises, node:process
//   - imported by:  ../../index.js
//
// Key exports:
//   - `isInteractive()` · `ask(rl, question, fallback)` · `choose(rl, question, options)`
//   - `askSecret(rl, question)` — never echoes what is typed (API key)
//   - `openPrompt()` — the readline interface (the caller closes it)

import { createInterface } from "node:readline/promises";
import { Writable } from "node:stream";
import process from "node:process";

/** Prompting in a pipeline or CI means hanging — there we continue with defaults. */
export function isInteractive() {
  return Boolean(process.stdin.isTTY && process.stdout.isTTY);
}

/**
 * A readline interface with switchable echo (`rl.mute(true/false)`).
 *
 * Echo is cut at the OUTPUT STREAM, not inside readline: leaning on a private method like
 * `_writeToOutput` silently stopped working on Node 26 (the method is gone) and we printed a
 * key to the screen believing masking was on. `output` is a documented option, and readline
 * must push every character through it.
 */
export function openPrompt({ input = process.stdin, output: sink = process.stdout, terminal } = {}) {
  let muted = false;
  const output = new Writable({
    write(chunk, encoding, done) {
      if (!muted) sink.write(chunk, encoding);
      done();
    },
  });
  // readline only does line editing (and echo) when `terminal` is on; our interposed stream
  // is not a tty, so we say so explicitly.
  output.columns = sink.columns;

  const rl = createInterface({ input, output, terminal: terminal ?? Boolean(sink.isTTY) });
  rl.mute = (on) => { muted = Boolean(on); };
  rl.echo = (text) => sink.write(text); // write past the mute (see askSecret)
  return rl;
}

/** An empty answer accepts the default. */
export async function ask(rl, question, fallback) {
  const answer = (await rl.question(`${question} ${fallback ? `(${fallback}) ` : ""}`)).trim();
  return answer === "" ? fallback : answer;
}

/**
 * The key prompt — not a single typed character is echoed.
 *
 * Why: we stopped recommending `--key` because the key survives on screen and in logs. If the
 * replacement path also printed the key to the terminal we would have gained nothing —
 * a shoulder surfer and a screen recording are the same leak.
 *
 * Order matters: the prompt is written SYNCHRONOUSLY inside `question()` while the echo
 * arrives with later keystrokes, so muting starts right after the call and the question stays
 * visible. If `mute` is absent (an interface not built by openPrompt) the question is still
 * asked: better a visible prompt than no way to ask for the key.
 */
export async function askSecret(rl, question) {
  const prompt = `${question} `;
  if (typeof rl.mute !== "function") return (await rl.question(prompt)).trim();

  const pending = rl.question(prompt);
  rl.mute(true);
  try {
    return (await pending).trim();
  } finally {
    rl.mute(false);
    (rl.echo ?? ((t) => process.stdout.write(t)))("\n"); // the newline from Enter was muted too
  }
}

/**
 * A numbered choice. Invalid input asks again — silently falling back to the default would
 * hand the user something other than what they think they picked.
 */
export async function choose(rl, question, options) {
  const lines = options.map((o, i) => `  ${i + 1}) ${o.label}${o.blurb ? ` — ${o.blurb}` : ""}`);
  process.stdout.write(`${question}\n${lines.join("\n")}\n`);

  for (;;) {
    const raw = (await rl.question(`Choose 1-${options.length} (1) `)).trim();
    if (raw === "") return options[0];

    const byIndex = Number.parseInt(raw, 10);
    if (Number.isInteger(byIndex) && byIndex >= 1 && byIndex <= options.length) return options[byIndex - 1];

    const byId = options.find((o) => o.id === raw.toLowerCase());
    if (byId) return byId;

    process.stdout.write(`  "${raw}" is not one of them.\n`);
  }
}
