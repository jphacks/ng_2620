// ブラウザ版（`pnpm dev` を Tauri なしで開いたとき）：メモリ上のファイル。再読み込みで消える

import type { FileStat } from "@/application/ports/media";
import type { FileSystem } from "./FileSystem";
import { readZip } from "./storeZip";
import { joinPath } from "./path";

export const MEM_ROOT = "/mem";

const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "");

export class MemoryFs implements FileSystem {
  private files = new Map<string, { blob: Blob; mtimeMs: number }>();
  private dirs = new Set<string>();
  private urls = new Map<string, string>();

  /** ドロップ・選択されたファイルを登録してパスを返す（中身はコピーしない） */
  putFile(file: File): string {
    const path = `${MEM_ROOT}/drops/${crypto.randomUUID().slice(0, 8)}/${file.name}`;
    this.setBlob(path, file);
    return path;
  }

  private setBlob(path: string, blob: Blob) {
    const p = norm(path);
    this.revoke(p);
    this.files.set(p, { blob, mtimeMs: Date.now() });
  }

  private revoke(p: string) {
    const u = this.urls.get(p);
    if (u) {
      URL.revokeObjectURL(u);
      this.urls.delete(p);
    }
  }

  private get(path: string) {
    const f = this.files.get(norm(path));
    if (!f) throw new Error(`ファイルがありません: ${path}`);
    return f;
  }

  async exists(path: string) {
    const p = norm(path);
    if (this.files.has(p) || this.dirs.has(p)) return true;
    for (const k of this.files.keys()) if (k.startsWith(`${p}/`)) return true;
    return false;
  }
  async stat(path: string): Promise<FileStat> {
    const f = this.get(path);
    return { size: f.blob.size, mtimeMs: f.mtimeMs };
  }
  async readText(path: string) {
    return this.get(path).blob.text();
  }
  async writeTextAtomic(path: string, text: string) {
    this.setBlob(path, new Blob([text], { type: "application/json" }));
  }
  async readBytes(path: string) {
    return new Uint8Array(await this.get(path).blob.arrayBuffer());
  }
  async readChunk(path: string, offset: number, length: number) {
    return new Uint8Array(await this.get(path).blob.slice(offset, offset + length).arrayBuffer());
  }
  async writeBytes(path: string, bytes: Uint8Array) {
    this.setBlob(path, new Blob([bytes]));
  }
  async copyFile(src: string, dest: string) {
    this.setBlob(dest, this.get(src).blob);
  }
  async ensureDir(path: string) {
    this.dirs.add(norm(path));
  }
  async listDir(path: string) {
    const p = `${norm(path)}/`;
    const names = new Set<string>();
    for (const k of [...this.files.keys(), ...this.dirs]) {
      if (k.startsWith(p)) names.add(k.slice(p.length).split("/")[0]!);
    }
    return [...names].filter(Boolean);
  }
  async remove(path: string) {
    const p = norm(path);
    for (const k of [...this.files.keys()]) {
      if (k === p || k.startsWith(`${p}/`)) {
        this.revoke(k);
        this.files.delete(k);
      }
    }
    for (const d of [...this.dirs]) if (d === p || d.startsWith(`${p}/`)) this.dirs.delete(d);
  }
  async moveToTrash(path: string) {
    await this.remove(path);
  }
  async unzip(zipPath: string, destDir: string) {
    for (const e of await readZip(await this.readBytes(zipPath))) {
      this.setBlob(joinPath(destDir, e.name), new Blob([e.bytes], { type: guessType(e.name) }));
    }
  }
  toUrl(path: string) {
    const p = norm(path);
    const hit = this.urls.get(p);
    if (hit) return hit;
    const f = this.files.get(p);
    if (!f) return "";
    const u = URL.createObjectURL(f.blob);
    this.urls.set(p, u);
    return u;
  }
}

function guessType(name: string): string {
  const ext = name.slice(name.lastIndexOf(".") + 1).toLowerCase();
  return { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", json: "application/json", mp4: "video/mp4" }[ext] ?? "";
}
