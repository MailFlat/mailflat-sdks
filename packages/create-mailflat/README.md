# create-mailflat

Scaffold an email-testing project wired to a real [MailFlat](https://mailflat.net) inbox.

```bash
npm create mailflat@latest
```

Asks three things — directory, runner, API key — and writes a project that runs.
The key is never echoed as you type it.

## Runners

| Template | What you get |
|---|---|
| `playwright` | A fixture that hands every test its own inbox, plus a browser signup spec |
| `vitest` | The same idea without a browser: API-level signup, reset and magic-link tests |
| `pytest` | A `conftest.py` fixture per test, plus a signup test |

## Non-interactive

Pass the key through the environment — `MAILFLAT_API_KEY` is read automatically, and it is
the same name the generated project uses:

```bash
npm create mailflat@latest my-tests -- --template pytest --yes
npx create-mailflat ci-mail -t vitest -y   # MAILFLAT_API_KEY already exported by CI
```

| Option | |
|---|---|
| `-t, --template <id>` | `playwright` · `vitest` · `pytest` |
| `-y, --yes` | accept defaults, ask nothing |
| `-f, --force` | write into a directory that already has files |
| `-k, --key <key>` | **discouraged** — see below |

### Don't put the key in the command

`-k / --key` still works, but a key typed on the command line ends up in three places you
cannot easily clean: your shell history, npm's echo of the command it ran, and npm's own
log file under `~/.npm/_logs/`. The CLI warns and tells you to revoke that key when it sees
the flag — because by then it has already leaked.

Use `MAILFLAT_API_KEY` from your CI secret store, or just answer the prompt.

## What it generates, and why

Two tests, on purpose:

- **`smoke`** runs green the moment you have a key. It opens a real inbox, checks the
  address came back, and deletes it — so a red smoke test means the key or the network,
  never your app.
- **`signup`** is the one you edit, and it is **skipped until `APP_URL` is set**. A signup
  test that passes without touching your app proves nothing, so it refuses to pretend.

Also: `.env` holds the key and is gitignored; `.env.example` holds a placeholder and is not.
Inboxes are created with `retentionHours: 2` and deleted in teardown, so a failed run does
not leave anything behind.

## Notes

- The address is permanent — only the messages inside expire. Your plan caps the retention
  window you can ask for.
- An end-to-end encrypted inbox cannot return a one-time code: the server never sees the
  plaintext. Leave test inboxes unencrypted.
- The generated project pulls [`@mailflat/sdk`](https://www.npmjs.com/package/@mailflat/sdk)
  or [`mailflat`](https://pypi.org/project/mailflat/); this scaffolder itself has zero
  dependencies.

Docs: <https://mailflat.net/docs>

## The examples repo is generated from here

The `mailflat-examples` tree is not written by hand. It is this package's output, so it
cannot drift from the templates that ship here:

```bash
npm run build:examples -- ../../../mailflat-examples
```

One template, two destinations. Maintaining the repo separately would guarantee the two
drift apart, which is the failure this project has already paid for twice.

## License

MIT
