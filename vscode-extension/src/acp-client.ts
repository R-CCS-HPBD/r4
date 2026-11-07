import { createInterface, type Interface as ReadLineInterface } from "node:readline";
import type { Readable, Writable } from "node:stream";

export type AcpParams = Record<string, unknown>;

export interface AcpNotification {
  jsonrpc: "2.0";
  method: string;
  params: AcpParams;
}

export interface AcpErrorData {
  code: number;
  message: string;
  data?: unknown;
}

interface PendingRequest {
  resolve: (result: unknown) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
  method: string;
  timeoutMs: number;
  requestToken?: string;
  abortCleanup?: () => void;
}

export interface AcpRequestOptions {
  timeoutMs?: number;
  signal?: AbortSignal;
}

export type AcpMethodHandler = (params: AcpParams) => Promise<unknown> | unknown;

export class AcpRequestError extends Error {
  constructor(
    message: string,
    readonly code: number,
    readonly data?: unknown,
  ) {
    super(message);
    this.name = "AcpRequestError";
  }
}

export class AcpClient {
  private readonly lines: ReadLineInterface;
  private readonly pending = new Map<number, PendingRequest>();
  private readonly requestTokens = new Map<string, number>();
  private readonly notificationListeners = new Set<(event: AcpNotification) => void>();
  private readonly protocolErrorListeners = new Set<(error: Error) => void>();
  private readonly methodHandlers = new Map<string, AcpMethodHandler>();
  private nextId = 1;
  private disposed = false;

  constructor(
    private readonly input: Readable,
    private readonly output: Writable,
    private readonly defaultTimeoutMs = 30_000,
  ) {
    this.lines = createInterface({ input });
    this.lines.on("line", (line) => this.handleLine(line));
    this.lines.on("close", () => this.dispose(new Error("R4 ACP output closed")));
    this.input.on("error", (error) => this.dispose(error));
    this.output.on("error", (error) => this.dispose(error));
  }

  request<T>(method: string, params: AcpParams = {}, options: AcpRequestOptions = {}): Promise<T> {
    if (this.disposed) {
      return Promise.reject(new Error("R4 ACP client is closed"));
    }
    if (!method.trim()) {
      return Promise.reject(new Error("ACP method must be non-empty"));
    }
    if (options.signal?.aborted) {
      return Promise.reject(abortError());
    }

    const id = this.nextId++;
    const timeoutMs = options.timeoutMs ?? this.defaultTimeoutMs;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => this.expire(id, method), timeoutMs);
      const pending: PendingRequest = {
        resolve: (result) => resolve(result as T),
        reject,
        timer,
        method,
        timeoutMs,
        requestToken: typeof params.request_id === "string" ? params.request_id : undefined,
      };
      if (options.signal) {
        const onAbort = () => {
          this.write({ jsonrpc: "2.0", method: "$/cancelRequest", params: { id } });
          this.rejectPending(id, abortError());
        };
        options.signal.addEventListener("abort", onAbort, { once: true });
        pending.abortCleanup = () => options.signal?.removeEventListener("abort", onAbort);
      }
      this.pending.set(id, pending);
      if (pending.requestToken) this.requestTokens.set(pending.requestToken, id);
      this.write({ jsonrpc: "2.0", id, method, params });
    });
  }

  onNotification(listener: (event: AcpNotification) => void): () => void {
    this.notificationListeners.add(listener);
    return () => this.notificationListeners.delete(listener);
  }

  onProtocolError(listener: (error: Error) => void): () => void {
    this.protocolErrorListeners.add(listener);
    return () => this.protocolErrorListeners.delete(listener);
  }

  registerMethod(method: string, handler: AcpMethodHandler): () => void {
    if (!method.trim() || this.methodHandlers.has(method)) {
      throw new Error(`ACP method registration rejected: ${method}`);
    }
    this.methodHandlers.set(method, handler);
    return () => this.methodHandlers.delete(method);
  }

  dispose(reason: Error = new Error("R4 ACP client disposed")): void {
    if (this.disposed) return;
    this.disposed = true;
    this.lines.close();
    for (const id of [...this.pending.keys()]) {
      this.rejectPending(id, reason);
    }
  }

  private write(message: object): void {
    this.output.write(`${JSON.stringify(message)}\n`, (error) => {
      if (error) this.dispose(error);
    });
  }

  private handleLine(line: string): void {
    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      this.reportProtocolError(new Error("R4 ACP emitted invalid JSON"));
      return;
    }
    if (!isRecord(message) || message.jsonrpc !== "2.0") {
      this.reportProtocolError(new Error("R4 ACP emitted an invalid JSON-RPC message"));
      return;
    }
    if (typeof message.method === "string" && message.id === undefined) {
      const notification: AcpNotification = {
        jsonrpc: "2.0",
        method: message.method,
        params: isRecord(message.params) ? message.params : {},
      };
      if (typeof notification.params.request_id === "string") {
        this.touchPendingRequest(notification.params.request_id);
      }
      for (const listener of this.notificationListeners) listener(notification);
      return;
    }
    if (typeof message.method === "string" && typeof message.id === "number") {
      void this.handleRequest(message.id, message.method, message.params);
      return;
    }
    if (typeof message.id === "number") {
      this.handleResponse(message.id, message);
      return;
    }
    this.reportProtocolError(new Error("R4 ACP message has no valid method or request id"));
  }

  private async handleRequest(id: number, method: string, params: unknown): Promise<void> {
    const handler = this.methodHandlers.get(method);
    if (!handler) {
      this.write({ jsonrpc: "2.0", id, error: { code: -32601, message: "Method not found" } });
      return;
    }
    try {
      const result = await handler(isRecord(params) ? params : {});
      this.write({ jsonrpc: "2.0", id, result });
    } catch {
      this.write({ jsonrpc: "2.0", id, error: { code: -32603, message: "Editor method failed" } });
    }
  }

  private handleResponse(id: number, message: Record<string, unknown>): void {
    const pending = this.takePending(id);
    if (!pending) return;
    if (isAcpError(message.error)) {
      pending.reject(new AcpRequestError(message.error.message, message.error.code, message.error.data));
      return;
    }
    pending.resolve(message.result);
  }

  private takePending(id: number): PendingRequest | undefined {
    const pending = this.pending.get(id);
    if (!pending) return undefined;
    this.pending.delete(id);
    if (pending.requestToken) this.requestTokens.delete(pending.requestToken);
    clearTimeout(pending.timer);
    pending.abortCleanup?.();
    return pending;
  }

  private expire(id: number, method: string): void {
    this.write({ jsonrpc: "2.0", method: "$/cancelRequest", params: { id } });
    this.rejectPending(id, new Error(`R4 ACP request timed out: ${method}`));
  }

  /**
   * Re-arm the timeout for the pending request that owns ``requestToken``.
   * Long agent turns stream server keepalives (``session/progress``) and live
   * events tagged with the request's ``request_id``; any such traffic proves
   * the agent is still working, so a healthy-but-long turn must not hit the
   * fixed per-request timeout.
   */
  private touchPendingRequest(requestToken: string): void {
    const id = this.requestTokens.get(requestToken);
    if (id === undefined) return;
    const pending = this.pending.get(id);
    if (!pending) return;
    clearTimeout(pending.timer);
    pending.timer = setTimeout(() => this.expire(id, pending.method), pending.timeoutMs);
  }

  private rejectPending(id: number, error: Error): void {
    this.takePending(id)?.reject(error);
  }

  private reportProtocolError(error: Error): void {
    for (const listener of this.protocolErrorListeners) listener(error);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAcpError(value: unknown): value is AcpErrorData {
  return isRecord(value) && typeof value.code === "number" && typeof value.message === "string";
}

function abortError(): Error {
  const error = new Error("R4 ACP request cancelled");
  error.name = "AbortError";
  return error;
}
