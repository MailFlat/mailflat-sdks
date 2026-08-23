# MailFlat SDKs

Official client libraries for [MailFlat](https://mailflat.net): email inboxes your code owns, with
**one-line OTP retrieval**. Built for test suites (Selenium, pytest, Playwright) and AI agents.

Addresses are **permanent**, so a suite does not need a fresh one per run. Only the messages
expire, on a retention window you choose.

> This repo holds only the open-source SDKs. The MailFlat service itself is closed-source.

## Packages

| Package | For | Install |
|---|---|---|
| [`mailflat`](https://pypi.org/project/mailflat/) | Python | `pip install mailflat` |
| [`@mailflat/sdk`](https://www.npmjs.com/package/@mailflat/sdk) | JavaScript / TypeScript | `npm i @mailflat/sdk` |
| [`mailflat-mcp`](https://pypi.org/project/mailflat-mcp/) | AI assistants (Claude, Cursor) — MCP | `uvx mailflat-mcp` |
| [`@mailflat/ai-sdk`](https://www.npmjs.com/package/@mailflat/ai-sdk) | Vercel AI SDK agents | `npm i @mailflat/ai-sdk` |
| `mailflat.langchain` | LangChain agents | `pip install "mailflat[langchain]"` |
| `mailflat-sdk` (Java) | Java / Selenium | via [JitPack](https://jitpack.io) — see below |

Each client is a thin, typed wrapper over the same `/api/v1` Agent API, so they behave identically.
Authenticate with your account key (`mf_live_…`) from the dashboard (**Agents → API keys**).

## Java / Selenium (JitPack)

> JitPack derives the `groupId` from the GitHub owner. This repo moved to the official
> `MailFlat` account in August 2026, so releases from `v0.4.4` on publish as
> `com.github.MailFlat:mailflat-sdks`. Earlier versions still resolve under the old
> coordinate `com.github.onderyentar21:mailflat-sdks`; the old repo stays up for them.
> The client is moving to Maven Central (`net.mailflat`) next, which ends the owner coupling.

```xml
<repositories>
  <repository><id>jitpack.io</id><url>https://jitpack.io</url></repository>
</repositories>

<dependency>
  <groupId>com.github.MailFlat</groupId>
  <artifactId>mailflat-sdks</artifactId>
  <version>v0.4.4</version>
</dependency>
```

```java
MailFlat mf = new MailFlat("mf_live_…");
Inbox inbox = mf.create("signup");
driver.findElement(By.id("email")).sendKeys(inbox.address());
String otp = inbox.waitForOtp(30);          // polls until the code arrives
driver.findElement(By.id("code")).sendKeys(otp);
```

## Quick examples

**Python**
```python
from mailflat import MailFlat
mf = MailFlat(api_key="mf_live_…")
inbox = mf.create(label="signup")
otp = inbox.wait_for_otp(timeout=30)
```

**JavaScript / TypeScript**
```ts
import { MailFlat } from "@mailflat/sdk";
const mf = new MailFlat({ apiKey: process.env.MAILFLAT_API_KEY });
const inbox = await mf.create({ label: "signup" });
const otp = await inbox.waitForOtp({ timeout: 30000 });
```

## License

MIT. The full text is in `LICENSE` at the repository root; each package directory carries an identical copy.
