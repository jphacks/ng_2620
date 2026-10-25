// HttpTransport のブラウザ実装（net.rs の移植、#25）。ローカルサーバー（同一オリジン）にも外部 GPU（CORS）にも使える。
//   download  成果物を <dest>.part に断片ごとに追記し、Range で再開し、最後に dest へ rename。丸ごとメモリに載せない
//   upload    断片の送信は HttpComputeBackend（readChunk → PUT）。ここでは持たない

import type { DownloadProgress } from "@/application/ports/compute";
import type { FileSystem } from "../fs/FileSystem";
import type { HttpTransport } from "./HttpComputeBackend";

/** .part へ書き出す単位。これより大きいバッファを抱えない */
export const FLUSH_BYTES = 4 * 1024 * 1024;

export interface FetchTransportOptions {
  /** 通信が途中で切れたときに、続きから取り直す回数 */
  retries?: number;
  backoffMs?: number;
  flushBytes?: number;
  sleep?: (ms: number) => Promise<void>;
  /** テスト用。既定は window.fetch */
  fetchImpl?: typeof fetch;
}

class HttpFailure extends Error {}

/** "bytes 100-199/1000" → 100 */
export function startFromContentRange(h: string | null): number | null {
  const m = /^\s*bytes\s+(\d+)-/.exec(h ?? "");
  return m ? Number(m[1]) : null;
}

/** "bytes 100-199/1000" → 1000 */
export function totalFromContentRange(h: string | null): number | null {
  const m = /\/(\d+)\s*$/.exec(h ?? "");
  return m ? Number(m[1]) : null;
}

export function fetchTransport(fs: FileSystem, opts: FetchTransportOptions = {}): HttpTransport {
  const { retries = 3, backoffMs = 500, flushBytes = FLUSH_BYTES } = opts;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const doFetch: typeof fetch = opts.fetchImpl ?? ((input, init) => window.fetch(input, init));

  /** .part に追記できない FS（制限モードの一部）：メモリに溜めて最後に書く */
  async function downloadBuffered(
    url: string,
    headers: Record<string, string>,
    dest: string,
    onProgress?: DownloadProgress,
  ) {
    const res = await doFetch(url, { headers });
    if (!res.ok) throw new HttpFailure(`HTTP ${res.status} ${await res.text().catch(() => "")}`);
    const total = Number(res.headers.get("content-length")) || null;
    const parts: Uint8Array[] = [];
    let received = 0;
    const reader = res.body?.getReader();
    for (;;) {
      const r = await reader?.read();
      if (!r || r.done) break;
      parts.push(r.value);
      received += r.value.length;
      onProgress?.(received, total);
    }
    const all = new Uint8Array(received);
    let at = 0;
    for (const p of parts) {
      all.set(p, at);
      at += p.length;
    }
    await fs.writeBytes(dest, all);
    onProgress?.(received, total ?? received);
  }

  async function downloadStreaming(
    url: string,
    headers: Record<string, string>,
    dest: string,
    onProgress?: DownloadProgress,
  ) {
    const append = fs.appendBytes!.bind(fs);
    const part = `${dest}.part`;
    for (let attempt = 0; ; attempt++) {
      let offset = (await fs.exists(part)) ? (await fs.stat(part)).size : 0;
      try {
        const res = await doFetch(url, { headers: offset ? { ...headers, Range: `bytes=${offset}-` } : headers });
        if (res.status === 416) {
          // .part が既に末尾まである（直前に完了していた）か、サーバー側のファイルが変わった
          const total = totalFromContentRange(res.headers.get("content-range"));
          if (total !== null && total === offset) break;
          await fs.remove(part);
          continue;
        }
        if (!res.ok) throw new HttpFailure(`HTTP ${res.status} ${await res.text().catch(() => "")}`);
        if (offset > 0 && (res.status !== 206 || startFromContentRange(res.headers.get("content-range")) !== offset)) {
          // Range を無視して全体が返ってきた、または違う位置から返ってきた：.part を捨てて最初から
          await fs.remove(part);
          if (res.status === 206) {
            await res.body?.cancel();
            continue;
          }
          offset = 0;
        }
        const total =
          res.status === 206
            ? totalFromContentRange(res.headers.get("content-range"))
            : Number(res.headers.get("content-length")) || null;
        onProgress?.(offset, total);
        let buf: Uint8Array[] = [];
        let buffered = 0;
        const flush = async () => {
          if (!buffered) return;
          const chunk = new Uint8Array(buffered);
          let at = 0;
          for (const b of buf) {
            chunk.set(b, at);
            at += b.length;
          }
          buf = [];
          buffered = 0;
          offset = await append(part, chunk, offset);
        };
        const reader = res.body?.getReader();
        for (;;) {
          const r = await reader?.read();
          if (!r || r.done) break;
          buf.push(r.value);
          buffered += r.value.length;
          if (buffered >= flushBytes) await flush();
          onProgress?.(offset + buffered, total);
        }
        await flush();
        if (total !== null && offset !== total) {
          throw new Error(`ダウンロードが途中で終わりました（${offset} / ${total} バイト）`);
        }
        break;
      } catch (e) {
        if (e instanceof HttpFailure || attempt >= retries) throw e;
        await sleep(backoffMs * 2 ** attempt); // 切れた：.part の続きから
      }
    }
    await fs.rename!(part, dest);
    const size = (await fs.stat(dest)).size;
    onProgress?.(size, size);
  }

  return {
    fetch: (input, init) => doFetch(input, init),
    download: (url, headers, dest, onProgress) =>
      fs.appendBytes && fs.rename
        ? downloadStreaming(url, headers, dest, onProgress)
        : downloadBuffered(url, headers, dest, onProgress),
    readChunk: (path, offset, length) => fs.readChunk(path, offset, length),
  };
}
