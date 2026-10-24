// ローカルの動画・ファイル（F02）

import type { LocalMediaInfo } from "@/domain/media";

export interface MediaProbePort {
  /** 動画のメタ情報（1 秒以内）。読めない項目は null */
  probe(path: string): Promise<LocalMediaInfo>;
}

export interface FileHasherPort {
  /** ストリームで sha256 を計算する。同じファイル（パス・サイズ・更新日時）の再計算は省く */
  sha256(path: string, onProgress?: (ratio: number) => void): Promise<string>;
}

export interface FileStat {
  size: number;
  mtimeMs: number;
}

/** ローカルファイルへの最小限のアクセス。大きなファイルは WebView に丸ごと載せない */
export interface LocalFilePort {
  exists(path: string): Promise<boolean>;
  stat(path: string): Promise<FileStat>;
  readText(path: string): Promise<string>;
  readBytes(path: string): Promise<Uint8Array>;
  readChunk(path: string, offset: number, length: number): Promise<Uint8Array>;
  writeBytes(path: string, bytes: Uint8Array): Promise<void>;
  /** <img> / <video> に渡せる URL（Tauri は asset プロトコル） */
  toUrl(path: string): string;
  join(base: string, ...parts: string[]): string;
}
