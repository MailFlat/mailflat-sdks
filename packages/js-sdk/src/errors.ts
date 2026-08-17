// MailFlat SDK error types — maps HTTP status codes to meaningful JS exceptions.
//
// Connected to:
//   - used by:    client.ts, inbox.ts, index.ts
//   - depends on: none (pure)
//
// Key export: MailFlatError and its subtypes (Authentication/Permission/NotFound/RateLimit/
// API/OTPTimeout/SendFailed/SendTimeout/EncryptedInbox)

export class MailFlatError extends Error {
  statusCode?: number;
  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = "MailFlatError";
    this.statusCode = statusCode;
    // Restores the prototype chain for Error subclasses under TS
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class AuthenticationError extends MailFlatError {
  constructor(message: string, statusCode?: number) {
    super(message, statusCode);
    this.name = "AuthenticationError";
  }
}

export class PermissionError extends MailFlatError {
  constructor(message: string, statusCode?: number) {
    super(message, statusCode);
    this.name = "PermissionError";
  }
}

export class NotFoundError extends MailFlatError {
  constructor(message: string, statusCode?: number) {
    super(message, statusCode);
    this.name = "NotFoundError";
  }
}

export class RateLimitError extends MailFlatError {
  /** The server's `Retry-After` in seconds, when it sent one. */
  retryAfter?: number;
  constructor(message: string, statusCode?: number, retryAfter?: number) {
    super(message, statusCode);
    this.name = "RateLimitError";
    this.retryAfter = retryAfter;
  }
}

export class APIError extends MailFlatError {
  constructor(message: string, statusCode?: number) {
    super(message, statusCode);
    this.name = "APIError";
  }
}

export class OTPTimeoutError extends MailFlatError {
  constructor(message: string) {
    super(message);
    this.name = "OTPTimeoutError";
  }
}

/**
 * Base for the two ways `waitUntilSent()` can end badly.
 *
 * Deliberately separate from OTPTimeoutError: that one means "the mail I was waiting for
 * never arrived", these mean "the mail I sent did not go out". One name for both would let
 * a single catch hide two unrelated faults.
 */
export class SendError extends MailFlatError {
  /** Id of the message being watched. */
  messageId?: number;
  /**
   * Last delivery status seen — "queued" (accepted, never attempted) or "retrying"
   * (attempted, not through yet) on timeout, "failed" on failure.
   */
  status?: string;
  /**
   * What the most recent delivery attempt reported, when there has been one. On a
   * timeout this separates "greylisted, try again in two minutes" from a problem no
   * amount of waiting will fix — the sentence alone described neither.
   */
  lastError?: string;
  constructor(message: string, messageId?: number, status?: string, lastError?: string) {
    super(message);
    this.name = "SendError";
    this.messageId = messageId;
    this.status = status;
    this.lastError = lastError;
  }
}

/** Delivery permanently failed; the queue will not retry it. */
export class SendFailedError extends SendError {
  constructor(message: string, messageId?: number, status?: string) {
    super(message, messageId, status);
    this.name = "SendFailedError";
  }
}

/**
 * Still queued when `waitUntilSent()` gave up.
 *
 * NOT the same as failure: the queue keeps retrying with exponential backoff, so the mail
 * may still be delivered. Greylisting alone can hold a mail for several minutes.
 */
export class SendTimeoutError extends SendError {
  constructor(message: string, messageId?: number, status?: string, lastError?: string) {
    super(message, messageId, status, lastError);
    this.name = "SendTimeoutError";
  }
}

export class EncryptedInboxError extends MailFlatError {
  constructor(message: string) {
    super(message);
    this.name = "EncryptedInboxError";
  }
}

// Throws the matching MailFlatError subclass for an HTTP status code.
export function raiseForStatus(
  statusCode: number,
  detail: string,
  retryAfter?: number,
): never {
  switch (statusCode) {
    case 401:
      throw new AuthenticationError(detail || "Invalid or missing API key", statusCode);
    case 403:
      throw new PermissionError(detail || "This API key does not own that inbox", statusCode);
    case 404:
      throw new NotFoundError(detail || "Not found", statusCode);
    case 429:
      throw new RateLimitError(detail || "Rate limit exceeded", statusCode, retryAfter);
    default:
      throw new APIError(detail || `Request failed with status ${statusCode}`, statusCode);
  }
}
