// SSE を fetch のストリームで読む（ADR-0012：EventSource はヘッダーを付けられないので使わない）。
// 行の解釈、Last-Event-ID での再開、指数バックオフ（最大 30 秒）、heartbeat の途絶の検知

import { AppError, isAppError } from "@/domain/errors";

export interface SseMessage {
  id?: string;
  event: string;
  data: string;
}

/** text/event-stream の行パーサ（断片の境界がどこで切れても良い） */
export class SseParser {
  private buf = "";
  private data: string[] = [];
  private event = "";
  private lastId: string | undefined;

  push(text: string): SseMessage[] {
    this.buf += text;
    const out: SseMessage[] = [];
    for (;;) {
      const idx = this.buf.search(/\r\n|\r|\n/);
      if (idx < 0) break;
      // 末尾の \r は次の断片の \n と組かもしれないので待つ
      if (this.buf[idx] === "\r" && idx === this.buf.length - 1) break;
      const len = this.buf[idx] === "\r" && this.buf[idx + 1] === "\n" ? 2 : 1;
      const line = this.buf.slice(0, idx);
      this.buf = this.buf.slice(idx + len);
      const msg = this.line(line);
      if (msg) out.push(msg);
    }
    return out;
  }

  private line(line: string): SseMessage | null {
    if (line === "") {
      if (this.data.length === 0) {
        this.event = "";
        return null;
      }
      const msg: SseMessage = { id: this.lastId, event: this.event || "message", data: this.data.join("\n") };
      this.data = [];
      this.event = "";
      return msg;
    }
    if (line.startsWith(":")) return null; // コメント
    const i = line.indexOf(":");
    const field = i < 0 ? line : line.slice(0, i);
    let value = i < 0 ? "" : line.slice(i + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    else if (field === "id" && !value.includes("\0")) this.lastId = value;
    return null;
  }
}

function withTimeout<T>(p: Promise<T>, ms: number | undefined): Promise<T> {
  if (!ms) return p;
  let timer: ReturnType<typeof setTimeout>;
  return Promise.race([
    p,
    new Promise<T>((_, reject) => {
      timer = setTimeout(() => reject(new AppError("NETWORK", "サーバーからの応答が途絶えました（heartbeat）")), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

/** ReadableStream から SSE のメッセージを順に取り出す。idleTimeoutMs の間なにも届かなければ切る */
export async function* readSse(
  body: ReadableStream<Uint8Array>,
  opts: { idleTimeoutMs?: number } = {},
): AsyncGenerator<SseMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  try {
    for (;;) {
      const r = await withTimeout(reader.read(), opts.idleTimeoutMs);
      if (r.done) return;
      for (const m of parser.push(decoder.decode(r.value, { stream: true }))) yield m;
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

export interface ResilientSseOptions {
  open(lastEventId: string | undefined, signal: AbortSignal): Promise<Response>;
  /** これを受け取ったら終了（再接続しない） */
  isTerminal(msg: SseMessage): boolean;
  lastEventId?: string;
  signal?: AbortSignal;
  /** heartbeat は 15 秒ごと。3 回分届かなければ切って再接続 */
  idleTimeoutMs?: number;
  /** 連続で失敗してよい回数（超えたら投げる → 呼び出し側がポーリングに落とす） */
  maxRetries?: number;
  toError?(res: Response): Promise<AppError>;
  sleep?(ms: number, signal?: AbortSignal): Promise<void>;
  onRetry?(attempt: number, error: unknown): void;
}

const fatal = (e: unknown) =>
  isAppError(e) && (e.code === "UNAUTHORIZED" || e.code === "NOT_FOUND" || e.detail?.fatal === true);

export function backoffMs(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.max(0, attempt - 1));
}

const defaultSleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(signal.reason);
    });
  });

/** 切れたら Last-Event-ID 付きでつなぎ直す SSE。終了イベントの後で終わる */
export async function* resilientSse(o: ResilientSseOptions): AsyncGenerator<SseMessage> {
  let lastId = o.lastEventId;
  let failures = 0;
  const maxRetries = o.maxRetries ?? 6;
  const sleep = o.sleep ?? defaultSleep;
  for (;;) {
    o.signal?.throwIfAborted();
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    o.signal?.addEventListener("abort", onAbort);
    let err: unknown;
    try {
      const res = await o.open(lastId, ctrl.signal);
      if (!res.ok) {
        err = o.toError
          ? await o.toError(res)
          : new AppError("NETWORK", `SSE の接続に失敗しました（HTTP ${res.status}）`, { status: res.status });
        if (fatal(err)) throw err;
      } else if (!res.body) {
        throw new AppError("NETWORK", "この環境では SSE のストリームを読めません", { detail: { fatal: true } });
      } else {
        for await (const msg of readSse(res.body, { idleTimeoutMs: o.idleTimeoutMs ?? 45_000 })) {
          failures = 0;
          if (msg.id) lastId = msg.id;
          yield msg;
          if (o.isTerminal(msg)) return;
        }
        err = new AppError("NETWORK", "SSE が途中で終わりました");
      }
    } catch (e) {
      if (o.signal?.aborted || fatal(e)) throw e;
      err = e;
    } finally {
      o.signal?.removeEventListener("abort", onAbort);
      ctrl.abort();
    }
    failures++;
    if (failures > maxRetries) throw err;
    o.onRetry?.(failures, err);
    await sleep(backoffMs(failures), o.signal);
  }
}
