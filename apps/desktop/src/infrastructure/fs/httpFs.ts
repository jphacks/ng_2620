// FileSystem のフルモード実装。ローカルアプリサーバーの /v1/local/fs を呼ぶ（#23・#26）。
// サーバーは許可されたフォルダの外を拒否する（PATH_NOT_ALLOWED）

import type { FileStat } from "@/application/ports/media";
import type { LocalApi } from "../local/localApi";
import type { FileSystem } from "./FileSystem";

const FS = "/fs";

export class HttpFileSystem implements FileSystem {
  constructor(private readonly api: LocalApi) {}

  async exists(path: string) {
    return (await this.api.json<{ exists: boolean }>("GET", `${FS}/exists`, { query: { path } })).exists;
  }
  async stat(path: string): Promise<FileStat> {
    const s = await this.api.json<{ size: number; mtimeMs: number }>("GET", `${FS}/stat`, { query: { path } });
    return { size: s.size, mtimeMs: s.mtimeMs };
  }
  async readText(path: string) {
    return (await this.api.request("GET", `${FS}/text`, { query: { path } })).text();
  }
  async writeTextAtomic(path: string, text: string, opts?: { private?: boolean }) {
    await this.api.request("PUT", `${FS}/text`, {
      query: { path, private: opts?.private ? true : undefined },
      body: text,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  async readBytes(path: string) {
    return new Uint8Array(await (await this.api.request("GET", `${FS}/raw`, { query: { path } })).arrayBuffer());
  }
  async readChunk(path: string, offset: number, length: number) {
    if (length <= 0) return new Uint8Array(0);
    const res = await this.api.request("GET", `${FS}/raw`, {
      query: { path },
      headers: { Range: `bytes=${offset}-${offset + length - 1}` },
    });
    if (res.status === 416) return new Uint8Array(0); // 末尾の外（fs.rs の read_range と同じく空）
    const bytes = new Uint8Array(await res.arrayBuffer());
    return res.status === 200 ? bytes.subarray(offset, offset + length) : bytes; // Range を無視する中継への備え
  }
  async writeBytes(path: string, bytes: Uint8Array) {
    await this.api.request("PUT", `${FS}/raw`, {
      query: { path },
      body: bytes,
      headers: { "Content-Type": "application/octet-stream" },
    });
  }
  async appendBytes(path: string, bytes: Uint8Array, expectedSize?: number) {
    const r = await this.api.json<{ size: number }>("POST", `${FS}/append`, {
      query: { path, expectedSize },
      body: bytes,
      headers: { "Content-Type": "application/octet-stream" },
    });
    return r.size;
  }
  async rename(src: string, dest: string) {
    await this.api.request("POST", `${FS}/rename`, { json: { src, dest } });
  }
  async copyFile(src: string, dest: string) {
    await this.api.request("POST", `${FS}/copy`, { json: { src, dest } });
  }
  async ensureDir(path: string) {
    await this.api.request("POST", `${FS}/mkdir`, { json: { path } });
  }
  async listDir(path: string) {
    return this.api.json<string[]>("GET", `${FS}/list`, { query: { path } });
  }
  async remove(path: string) {
    await this.api.request("POST", `${FS}/remove`, { json: { path } });
  }
  async moveToTrash(path: string) {
    await this.api.request("POST", `${FS}/trash`, { json: { path } });
  }
  async unzip(zipPath: string, destDir: string) {
    await this.api.request("POST", `${FS}/unzip`, { json: { zipPath, destDir } });
  }
  toUrl(path: string) {
    return this.api.rawUrl(path);
  }
}
