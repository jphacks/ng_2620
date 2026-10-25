// FileHasherPort の実装。フルモードはローカルサーバー（進捗は SSE）／制限モードは WebCrypto

import type { FileHasherPort } from "@/application/ports/media";
import type { FileSystem } from "../fs/FileSystem";
import { SseParser } from "../compute/sse";
import type { LocalApi } from "../local/localApi";

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export class WebCryptoFileHasher implements FileHasherPort {
  private cache = new Map<string, string>();
  constructor(private readonly fs: FileSystem) {}
  async sha256(path: string, onProgress?: (ratio: number) => void): Promise<string> {
    const st = await this.fs.stat(path);
    const key = `${path}:${st.size}:${st.mtimeMs}`;
    const hit = this.cache.get(key);
    if (hit) return hit;
    onProgress?.(0);
    const h = await sha256Hex(await this.fs.readBytes(path));
    onProgress?.(1);
    this.cache.set(key, h);
    return h;
  }
}

/** フルモード：ローカルサーバーがストリームで計算する（POST /v1/local/hash/sha256）。進捗を求められたら SSE で受ける */
export class HttpFileHasher implements FileHasherPort {
  constructor(private readonly api: LocalApi) {}

  async sha256(path: string, onProgress?: (ratio: number) => void): Promise<string> {
    if (!onProgress) {
      return (await this.api.json<{ sha256: string }>("POST", "/hash/sha256", { json: { path } })).sha256;
    }
    const res = await this.api.request("POST", "/hash/sha256", {
      json: { path },
      headers: { Accept: "text/event-stream" },
    });
    const reader = res.body?.getReader();
    if (!reader) throw new Error("進捗を読めませんでした");
    const parser = new SseParser();
    const dec = new TextDecoder();
    for (;;) {
      const r = await reader.read();
      if (r.done) break;
      for (const m of parser.push(dec.decode(r.value, { stream: true }))) {
        if (m.event === "progress") onProgress((JSON.parse(m.data) as { ratio: number }).ratio);
        else if (m.event === "done") {
          await reader.cancel();
          return (JSON.parse(m.data) as { sha256: string }).sha256;
        }
      }
    }
    throw new Error("ハッシュの計算が途中で終わりました");
  }
}
