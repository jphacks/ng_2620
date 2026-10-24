// Tauri 版：src-tauri/src/commands/fs.rs のコマンドを呼ぶ

import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import type { FileStat } from "@/application/ports/media";
import type { FileSystem } from "./FileSystem";

export const tauriFs: FileSystem = {
  exists: (path) => invoke<boolean>("fs_exists", { path }),
  stat: (path) => invoke<FileStat>("fs_stat", { path }),
  readText: (path) => invoke<string>("fs_read_text", { path }),
  writeTextAtomic: (path, contents, opts) => invoke("fs_write_atomic", { path, contents, private: opts?.private ?? false }),
  readBytes: async (path) => new Uint8Array(await invoke<ArrayBuffer>("fs_read_bytes", { path })),
  readChunk: async (path, offset, length) => new Uint8Array(await invoke<ArrayBuffer>("fs_read_chunk", { path, offset, length })),
  // 本体はそのままバイト列で渡す（JSON に変換しない）。パスはヘッダーで
  writeBytes: (path, bytes) => invoke("fs_write_bytes", bytes, { headers: { "x-path": encodeURIComponent(path) } }),
  copyFile: (src, dest) => invoke("fs_copy_file", { src, dest }),
  ensureDir: (path) => invoke("fs_ensure_dir", { path }),
  listDir: (path) => invoke<string[]>("fs_list_dir", { path }),
  remove: (path) => invoke("fs_remove", { path }),
  moveToTrash: (path) => invoke("fs_move_to_trash", { path }),
  unzip: (zipPath, destDir) => invoke("fs_unzip", { zipPath, destDir }),
  toUrl: (path) => convertFileSrc(path),
};
