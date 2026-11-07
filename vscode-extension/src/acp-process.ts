import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { AcpClient, type AcpParams, type AcpRequestOptions } from "./acp-client";

export interface AcpProcessOptions {
  command: string;
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  requestTimeoutMs?: number;
  spawnProcess?: typeof spawn;
  killTimeoutMs?: number;
}

export class AcpProcessSupervisor {
  private process: ChildProcessWithoutNullStreams | undefined;
  private client: AcpClient | undefined;
  private stopped = false;
  private readonly stderrTail: string[] = [];

  constructor(private readonly options: AcpProcessOptions) {}

  async start(): Promise<AcpClient> {
    if (this.client) return this.client;
    if (this.stopped) throw new Error("R4 ACP supervisor is stopped");

    const spawnProcess = this.options.spawnProcess ?? spawn;
    const child = spawnProcess(this.options.command, ["acp"], {
      cwd: this.options.cwd,
      env: this.options.env,
      shell: false,
      detached: process.platform !== "win32",
      stdio: "pipe",
    });
    this.process = child;
    child.stderr.setEncoding("utf8");
    child.stderr.resume();
    // The agent logs handler failures to stderr; keeping a short tail makes
    // surprise exits diagnosable instead of silently discarding the output.
    child.stderr.on("data", (chunk: string) => this.captureStderr(chunk));

    const client = new AcpClient(child.stdout, child.stdin, this.options.requestTimeoutMs);
    this.client = client;
    child.once("error", (error) => this.handleExit(error));
    child.once("exit", (code, signal) => {
      const status = signal ? `signal ${signal}` : `code ${code ?? "unknown"}`;
      const tail = this.formatStderrTail();
      this.handleExit(new Error(`R4 ACP process exited with ${status}${tail}`));
    });

    try {
      const init = await client.request<Record<string, unknown>>(
        "initialize",
        {},
        { timeoutMs: this.options.requestTimeoutMs },
      );
      const source = init?.source;
      if (source && typeof source === "object") {
        this.sourceIdentity = source as { digest: string; source_dir: string; git_commit?: string | null };
      }
      return client;
    } catch (error) {
      this.terminate(child);
      this.handleExit(error instanceof Error ? error : new Error(String(error)));
      throw error;
    }
  }

  /** Source identity from the ACP initialize handshake (task 7). */
  sourceIdentity?: { digest: string; source_dir: string; git_commit?: string | null };

  async request<T>(
    method: string,
    params: AcpParams = {},
    options: AcpRequestOptions = {},
  ): Promise<T> {
    const client = await this.start();
    return client.request<T>(method, params, options);
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    const client = this.client;
    const child = this.process;
    this.client = undefined;
    this.process = undefined;
    client?.dispose(new Error("R4 ACP process stopped"));
    if (child) this.terminate(child);
  }

  dispose(): void {
    this.stop();
  }

  private handleExit(error: Error): void {
    const client = this.client;
    this.client = undefined;
    this.process = undefined;
    client?.dispose(error);
  }

  private terminate(child: ChildProcessWithoutNullStreams): void {
    if (child.exitCode !== null || child.signalCode !== null) return;
    signalProcessTree(child, "SIGTERM");
    const timer = setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) signalProcessTree(child, "SIGKILL");
    }, this.options.killTimeoutMs ?? 2_000);
    timer.unref();
    child.once("exit", () => clearTimeout(timer));
  }
  private captureStderr(chunk: string): void {
    for (const line of chunk.split(/\r?\n/)) {
      if (!line) continue;
      this.stderrTail.push(line);
      if (this.stderrTail.length > 50) this.stderrTail.shift();
    }
  }

  private formatStderrTail(): string {
    if (this.stderrTail.length === 0) return "";
    const tail = this.stderrTail.slice(-10).join(" | ");
    return ` — stderr: ${tail.slice(0, 500)}`;
  }}

function signalProcessTree(
  child: ChildProcessWithoutNullStreams,
  signal: NodeJS.Signals,
): void {
  if (process.platform !== "win32" && child.pid) {
    try {
      process.kill(-child.pid, signal);
      return;
    } catch {
      // Child may have exited or may not own a process group.
    }
  }
  child.kill(signal);
}
