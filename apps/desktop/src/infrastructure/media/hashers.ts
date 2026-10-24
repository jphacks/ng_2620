// FileHasherPort の実装。Tauri は Rust（sha2）でストリーム計算、ブラウザは WebCrypto

import { Channel, invoke } from "@tauri-apps/api/core";
import type { FileHasherPort } from "@/application/ports/media";
import type { FileSystem } from "../fs/FileSystem";

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export class TauriFileHasher implements FileHasherPort {
  async sha256(path: string, onProgress?: (ratio: number) => void): Promise<string> {
    const ch = new Channel<number>();
    if (onProgress) ch.onmessage = onProgress;
    return invoke<string>("hash_sha256", { path, onProgress: ch });
  }
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
