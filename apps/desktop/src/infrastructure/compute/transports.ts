// HTTP の下回り。Tauri：tauri-plugin-http＋Rust のダウンロード・アップロード（net.rs）／ブラウザ：fetch（fetchTransport.ts）

import { Channel, invoke } from "@tauri-apps/api/core";
import { fetch as tauriFetch } from "@tauri-apps/plugin-http";
import type { DownloadProgress } from "@/application/ports/compute";
import type { FileSystem } from "../fs/FileSystem";
import { fetchTransport } from "./fetchTransport";
import type { HttpTransport } from "./HttpComputeBackend";

export function tauriTransport(fs: FileSystem): HttpTransport {
  return {
    fetch: tauriFetch as typeof fetch,
    async download(url, headers, dest, onProgress?: DownloadProgress) {
      const ch = new Channel<{ received: number; total: number | null }>();
      if (onProgress) ch.onmessage = (p) => onProgress(p.received, p.total);
      await invoke("net_download", { url, headers, dest, onProgress: ch });
    },
    uploadFile: (url, headers, path, offset, length) =>
      invoke<{ status: number; body: string }>("net_upload_chunk", { url, headers, path, offset, length }),
    readChunk: (path, offset, length) => fs.readChunk(path, offset, length),
  };
}

export function browserTransport(fs: FileSystem): HttpTransport {
  return fetchTransport(fs);
}
