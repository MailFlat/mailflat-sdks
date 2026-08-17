// MailFlat client — a thin, typed JS/TS wrapper around the /api/v1 automation API.
//
// One account API key (X-API-Key) creates inboxes, lists them, reads mail/OTP and sends mail.
// Uses global fetch (Node 18+ / modern browsers) — zero dependencies.
//
// Connected to:
//   - used by:    index.ts, user code, @mailflat/ai-sdk
//   - depends on: inbox.ts, errors.ts, types.ts
//
// Key export: MailFlat

import { MailFlatError, raiseForStatus } from "./errors";
import { Inbox } from "./inbox";
import { buildPayload, CREATE_INBOX_FIELDS } from "./payload";
import type { CreateInboxOptions } from "./types";
import { VERSION } from "./version";

const DEFAULT_BASE_URL = "https://mailflat.net";
const RETRY_STATUSES = new Set([429, 502, 503, 504]);

// Only side-effect-free (idempotent) methods are retried. POST is deliberately excluded:
// if the MTA accepts a /send and the gateway then returns 504, a retry delivers THE SAME
// MAIL TWICE and the user never finds out.
const IDEMPOTENT_METHODS = new Set(["GET", "HEAD", "DELETE"]);
const MAX_BACKOFF_MS = 8000;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Parse a `Retry-After` header (seconds form) into ms. Null when absent/unparsable. */
function retryAfterMs(resp: Response): number | null {
  const raw = resp.headers?.get?.("Retry-After");
  if (!raw) return null;
  const secs = Number(raw.trim());
  if (!Number.isFinite(secs) || secs < 0) return null; // HTTP-date form → we do not guess
  return secs * 1000;
}

export interface MailFlatOptions {
  apiKey?: string;
  baseUrl?: string;
  timeout?: number; // ms (per request)
  maxRetries?: number;
  fetch?: typeof fetch; // injectable for tests
}

export class MailFlat {
  // `!` because the value is installed with defineProperty (non-enumerable) rather than a
  // plain assignment — the type is still `string`, tsc just cannot see the write.
  readonly apiKey!: string;
  readonly baseUrl: string;
  private timeout: number;
  private maxRetries: number;
  private _fetch: typeof fetch;

  constructor(opts: MailFlatOptions = {}) {
    const apiKey =
      opts.apiKey ??
      (typeof process !== "undefined" ? process.env?.MAILFLAT_API_KEY : undefined);
    if (!apiKey) {
      throw new MailFlatError(
        "An API key is required. Pass new MailFlat({ apiKey }) or set MAILFLAT_API_KEY.",
      );
    }
    // Non-enumerable: readable as `mf.apiKey` (some callers do), invisible to
    // `JSON.stringify`, `console.log` and `util.inspect`. Second layer behind Inbox's
    // `#client` — a MailFlat instance can be logged directly too.
    Object.defineProperty(this, "apiKey", {
      value: apiKey, enumerable: false, writable: false, configurable: false,
    });
    this.baseUrl = (opts.baseUrl ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.timeout = opts.timeout ?? 30000;
    this.maxRetries = Math.max(0, opts.maxRetries ?? 2);
    const f = opts.fetch ?? (typeof fetch !== "undefined" ? fetch : undefined);
    if (!f) {
      throw new MailFlatError(
        "No fetch implementation found. Use Node 18+ or pass { fetch } in options.",
      );
    }
    this._fetch = f;
  }

  /** The `User-Agent` this client sends, e.g. `mailflat-js/<version>`. */
  static userAgent(): string {
    return `mailflat-js/${VERSION}`;
  }

  private _headers(hasBody: boolean): Record<string, string> {
    return {
      "X-API-Key": this.apiKey,
      "User-Agent": MailFlat.userAgent(),
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
    };
  }

  private async _fetchOnce(method: string, path: string, body?: Record<string, any>) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    try {
      return await this._fetch(`${this.baseUrl}${path}`, {
        method,
        headers: this._headers(Boolean(body)),
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } catch (err: any) {
      throw new MailFlatError(`Network error: ${err?.message ?? err}`);
    } finally {
      clearTimeout(timer);
    }
  }

  // ------------------------------------------------------------- HTTP internals
  /**
   * `idempotent = true` marks a POST as safe to repeat (markRead/burn). Never passed for
   * send: a repeated send delivers the same mail twice.
   */
  async _request(
    method: string,
    path: string,
    body?: Record<string, any>,
    idempotent?: boolean,
  ): Promise<any> {
    let attempt = 0;
    for (;;) {
      const resp = await this._fetchOnce(method, path, body);
      const waitMs = retryAfterMs(resp);

      const canRepeat = idempotent ?? IDEMPOTENT_METHODS.has(method.toUpperCase());
      const retriable =
        RETRY_STATUSES.has(resp.status) && canRepeat && attempt < this.maxRetries;
      if (retriable) {
        attempt += 1;
        const backoff = waitMs ?? Math.min(2 ** attempt * 500, MAX_BACKOFF_MS);
        await sleep(Math.min(backoff, MAX_BACKOFF_MS));
        continue;
      }

      let data: any = {};
      try {
        data = await resp.json();
      } catch {
        data = {};
      }

      if (resp.status >= 400) {
        const detail =
          (data && (data.detail || data.error)) || `Request failed with status ${resp.status}`;
        raiseForStatus(resp.status, detail, waitMs == null ? undefined : waitMs / 1000);
      }
      if (data && typeof data === "object" && data.error) {
        throw new MailFlatError(data.error, resp.status);
      }
      return data ?? {};
    }
  }

  /**
   * Raw bytes for binary endpoints (attachment download).
   * Retries like any other GET — this path skipped the retry loop entirely until 0.3.1.
   */
  async _getBytes(path: string): Promise<Uint8Array> {
    let attempt = 0;
    let resp = await this._fetchOnce("GET", path);
    while (RETRY_STATUSES.has(resp.status) && attempt < this.maxRetries) {
      attempt += 1;
      const waitMs = retryAfterMs(resp);
      await sleep(Math.min(waitMs ?? Math.min(2 ** attempt * 500, MAX_BACKOFF_MS), MAX_BACKOFF_MS));
      resp = await this._fetchOnce("GET", path);
    }
    if (resp.status >= 400) {
      let detail = "";
      try {
        const body: any = await resp.json();
        detail = body?.detail || body?.error || "";
      } catch {
        /* body is not JSON — detail stays empty */
      }
      const waitMs = retryAfterMs(resp);
      raiseForStatus(resp.status, detail, waitMs == null ? undefined : waitMs / 1000);
    }
    const ctype = resp.headers?.get?.("content-type") ?? "";
    if (ctype.includes("application/json")) {
      // On an encrypted inbox the server cannot decrypt: it returns the envelope JSON
      // rather than the bytes.
      throw new MailFlatError(
        "This inbox is end-to-end encrypted; the server cannot decrypt this attachment.",
      );
    }
    return new Uint8Array(await resp.arrayBuffer());
  }

  _get(path: string) {
    return this._request("GET", path);
  }
  _post(path: string, body: Record<string, any>, idempotent = false) {
    return this._request("POST", path, body, idempotent);
  }
  _delete(path: string) {
    return this._request("DELETE", path);
  }

  // -------------------------------------------------------------- public
  /** Create a new inbox and return it. */
  async create(opts: CreateInboxOptions = {}): Promise<Inbox> {
    // Unknown fields go to the server, they are not dropped here — see payload.ts.
    const payload = buildPayload(opts, CREATE_INBOX_FIELDS);
    const res = await this._post("/api/v1/inboxes", payload);
    const { address, ...meta } = res;
    return new Inbox(this, address, meta);
  }

  /** Alias of `create()`, matching the landing-page snippets. */
  createInbox(opts: CreateInboxOptions = {}): Promise<Inbox> {
    return this.create(opts);
  }

  /** Return the inboxes created with this API key. */
  async list(): Promise<Inbox[]> {
    const res = await this._get("/api/v1/inboxes");
    return ((res.inboxes as any[]) || []).map((i) => {
      const { address, ...meta } = i;
      return new Inbox(this, address, meta);
    });
  }

  /** Attach to an existing address without making an API call. */
  inbox(address: string): Inbox {
    return new Inbox(this, address);
  }
}
