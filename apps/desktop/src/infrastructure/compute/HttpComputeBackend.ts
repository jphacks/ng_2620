// ComputeBackendPort の HTTP 実装（ローカル・リモート・モックサーバー共通、contracts §2）。
// HTTP は window.fetch（外部 GPU には CORS・HTTPS・トークンが要る。ADR-0015）

import type { ChunkBody, ComputeBackendPort, CreateUploadReq, DownloadProgress } from "@/application/ports/compute";
import type { ApiErrorBody } from "@/domain/errors";
import { AppError, fromApiError, isAppError } from "@/domain/errors";
import type { Artifact, JobEvent, JobParams, JobType } from "@/domain/job";
import { JOB_EVENT_TYPES } from "@/domain/job";
import type { MediaMeta } from "@/domain/media";
import {
  normalizeArtifacts,
  normalizeCapabilities,
  normalizeHealth,
  normalizeJob,
  normalizeQueue,
  normalizeServerProject,
} from "./normalize";
import type { SseMessage } from "./sse";
import { resilientSse } from "./sse";

export interface HttpTransport {
  fetch: typeof fetch;
  /** 成果物を dest に保存（fetchTransport は <dest>.part に追記し、Range で再開する） */
  download(url: string, headers: Record<string, string>, dest: string, onProgress?: DownloadProgress): Promise<void>;
  /** ファイルの範囲をそのまま PUT する実装があれば使う。無ければ readChunk → fetch */
  uploadFile?(
    url: string,
    headers: Record<string, string>,
    path: string,
    offset: number,
    length: number,
  ): Promise<{ status: number; body: string }>;
  readChunk(path: string, offset: number, length: number): Promise<Uint8Array>;
}

const STATUS_CODES: Record<number, AppError["code"]> = {
  401: "UNAUTHORIZED",
  403: "UNAUTHORIZED",
  404: "NOT_FOUND",
  409: "UPLOAD_INCOMPLETE",
  422: "VALIDATION_FAILED",
  501: "NOT_IMPLEMENTED",
};

export function errorFromBody(status: number, text: string): AppError {
  try {
    const body = JSON.parse(text) as { error?: ApiErrorBody };
    if (body.error?.code) return fromApiError(body.error, status);
  } catch {
    // JSON でない本文
  }
  const code = STATUS_CODES[status] ?? "UNKNOWN";
  return new AppError(code, text.slice(0, 200) || `HTTP ${status}`, { status });
}

const TERMINAL_EVENTS = new Set(["job_finished", "job_failed"]);

interface SendOpts {
  json?: unknown;
  body?: BodyInit;
  auth?: boolean;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export class HttpComputeBackend implements ComputeBackendPort {
  private readonly base: string;

  constructor(
    private readonly d: {
      baseUrl: string;
      profileId: string | null;
      getToken: () => Promise<string | null>;
      transport: HttpTransport;
      /** 断片の送信が通信エラーで失敗したときの再試行（PUT は断片番号ごとに冪等）。既定は 3 回・0.5 秒から倍々 */
      uploadRetry?: { tries?: number; backoffMs?: number; sleep?: (ms: number) => Promise<void> };
    },
  ) {
    this.base = `${d.baseUrl.replace(/\/+$/, "")}/v1`;
  }

  get profileId() {
    return this.d.profileId;
  }

  private async headers(auth: boolean, extra: Record<string, string> = {}): Promise<Record<string, string>> {
    const h: Record<string, string> = { ...extra };
    if (auth) {
      const token = await this.d.getToken();
      if (token) h.Authorization = `Bearer ${token}`;
    }
    return h;
  }

  private async send(method: string, path: string, opts: SendOpts = {}): Promise<Response> {
    const headers = await this.headers(opts.auth ?? true, opts.headers);
    let body = opts.body;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(opts.json);
    }
    let res: Response;
    try {
      res = await this.d.transport.fetch(`${this.base}${path}`, { method, headers, body, signal: opts.signal });
    } catch (e) {
      if (opts.signal?.aborted) throw e;
      throw new AppError("NETWORK", e instanceof Error ? e.message : String(e));
    }
    if (!res.ok) throw errorFromBody(res.status, await res.text().catch(() => ""));
    return res;
  }

  private async json<T>(method: string, path: string, opts?: SendOpts): Promise<T> {
    const res = await this.send(method, path, opts);
    const text = await res.text();
    return (text ? JSON.parse(text) : {}) as T;
  }

  async health() {
    return normalizeHealth(await this.json("GET", "/health", { auth: false }));
  }

  async capabilities() {
    return normalizeCapabilities(await this.json("GET", "/capabilities"));
  }

  async createProject(name: string) {
    return this.json<{ projectId: string }>("POST", "/projects", { json: { name } });
  }

  async getProject(projectId: string) {
    return normalizeServerProject(await this.json("GET", `/projects/${encodeURIComponent(projectId)}`), projectId);
  }

  async deleteProject(projectId: string) {
    await this.send("DELETE", `/projects/${encodeURIComponent(projectId)}`);
  }

  async createUpload(req: CreateUploadReq) {
    return this.json<{ uploadId: string; chunkSize: number }>("POST", "/uploads", { json: req });
  }

  async uploadChunk(uploadId: string, index: number, body: ChunkBody) {
    const {
      tries = 3,
      backoffMs = 500,
      sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms)),
    } = this.d.uploadRetry ?? {};
    for (let attempt = 1; ; attempt++) {
      try {
        return await this.uploadChunkOnce(uploadId, index, body);
      } catch (e) {
        if (!isAppError(e, "NETWORK") || attempt >= tries) throw e;
        await sleep(backoffMs * 2 ** (attempt - 1));
      }
    }
  }

  private async uploadChunkOnce(uploadId: string, index: number, body: ChunkBody) {
    const path = `/uploads/${encodeURIComponent(uploadId)}/chunks/${index}`;
    const contentType = { "Content-Type": "application/octet-stream" };
    if (body instanceof Uint8Array) {
      await this.send("PUT", path, { body, headers: contentType });
      return;
    }
    const { transport } = this.d;
    if (transport.uploadFile) {
      const res = await transport
        .uploadFile(`${this.base}${path}`, await this.headers(true, contentType), body.path, body.offset, body.length)
        .catch((e: unknown) => {
          throw new AppError("NETWORK", e instanceof Error ? e.message : String(e));
        });
      if (res.status < 200 || res.status >= 300) throw errorFromBody(res.status, res.body);
      return;
    }
    const bytes = await transport.readChunk(body.path, body.offset, body.length);
    await this.send("PUT", path, { body: bytes, headers: contentType });
  }

  async getUpload(uploadId: string) {
    return this.json<{ receivedChunks: number[]; size: number }>("GET", `/uploads/${encodeURIComponent(uploadId)}`);
  }

  async completeUpload(uploadId: string) {
    return this.json<{ mediaId: string; meta: MediaMeta }>("POST", `/uploads/${encodeURIComponent(uploadId)}/complete`);
  }

  async registerAvatar(file: { name: string; bytes: Uint8Array }) {
    const form = new FormData();
    form.append("file", new Blob([file.bytes], { type: "model/gltf-binary" }), file.name);
    return this.json<{ avatarId: string }>("POST", "/avatars", { body: form });
  }

  async hasAvatar(avatarId: string) {
    try {
      await this.send("GET", `/avatars/${encodeURIComponent(avatarId)}`);
      return true;
    } catch (e) {
      if (isAppError(e, "NOT_FOUND")) return false;
      throw e;
    }
  }

  async submitJob(projectId: string, type: JobType, params: JobParams) {
    return normalizeJob(
      await this.json("POST", `/projects/${encodeURIComponent(projectId)}/jobs`, { json: { type, params } }),
    );
  }

  async getJob(jobId: string) {
    return normalizeJob(await this.json("GET", `/jobs/${encodeURIComponent(jobId)}`));
  }

  async listJobs(projectId: string) {
    const raw = await this.json<unknown>("GET", `/projects/${encodeURIComponent(projectId)}/jobs`);
    return (Array.isArray(raw) ? raw : ((raw as { items?: unknown[] }).items ?? [])).map(normalizeJob);
  }

  async queue() {
    return normalizeQueue(await this.json("GET", "/queue"));
  }

  async cancelJob(jobId: string) {
    await this.send("POST", `/jobs/${encodeURIComponent(jobId)}/cancel`);
  }

  async *events(jobId: string, opts: { lastEventId?: string; signal?: AbortSignal } = {}): AsyncGenerator<JobEvent> {
    const url = `${this.base}/jobs/${encodeURIComponent(jobId)}/events`;
    const stream = resilientSse({
      lastEventId: opts.lastEventId,
      signal: opts.signal,
      isTerminal: (m) => TERMINAL_EVENTS.has(m.event),
      open: async (lastId, signal) => {
        const headers = await this.headers(true, {
          Accept: "text/event-stream",
          ...(lastId ? { "Last-Event-ID": lastId } : {}),
        });
        return this.d.transport.fetch(url, { method: "GET", headers, signal });
      },
      toError: async (res) => errorFromBody(res.status, await res.text().catch(() => "")),
      onRetry: (n, e) => console.warn(`[sse] ${jobId} 再接続 ${n} 回目`, e),
    });
    for await (const m of stream) {
      const ev = toJobEvent(m);
      if (ev) yield ev;
    }
  }

  async listArtifacts(jobId: string): Promise<Artifact[]> {
    return normalizeArtifacts(await this.json("GET", `/jobs/${encodeURIComponent(jobId)}/artifacts`));
  }

  async downloadArtifact(jobId: string, name: string, dest: string, onProgress?: DownloadProgress) {
    const url = `${this.base}/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(name)}`;
    try {
      await this.d.transport.download(url, await this.headers(true), dest, onProgress);
    } catch (e) {
      if (e instanceof AppError) throw e;
      const msg = e instanceof Error ? e.message : String(e);
      // transport は "HTTP <status> <本文>" の形で投げる（net.rs / browserTransport）
      const m = /HTTP (\d{3}) ?([\s\S]*)/.exec(msg);
      throw m ? errorFromBody(Number(m[1]), m[2] ?? "") : new AppError("NETWORK", msg);
    }
  }

  async logs(jobId: string, tail = 200) {
    const res = await this.send("GET", `/jobs/${encodeURIComponent(jobId)}/logs?tail=${tail}`);
    return res.text();
  }
}

export function toJobEvent(m: SseMessage): JobEvent | null {
  if (!(JOB_EVENT_TYPES as readonly string[]).includes(m.event)) return null;
  let data: unknown = {};
  try {
    data = m.data ? JSON.parse(m.data) : {};
  } catch {
    return null;
  }
  if (m.event === "snapshot") data = normalizeJob(data);
  return { id: m.id, type: m.event, data } as JobEvent;
}
