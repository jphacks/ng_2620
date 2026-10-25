// infrastructure 内で共有するファイル操作の抽象。フルモード（ローカルサーバー）と制限モード（IndexedDB／メモリ）の実装

import type { FileStat } from "@/application/ports/media";

export interface FileSystem {
  exists(path: string): Promise<boolean>;
  stat(path: string): Promise<FileStat>;
  readText(path: string): Promise<string>;
  /** 一時ファイル → rename。private は権限 600（macOS / Linux） */
  writeTextAtomic(path: string, text: string, opts?: { private?: boolean }): Promise<void>;
  readBytes(path: string): Promise<Uint8Array>;
  readChunk(path: string, offset: number, length: number): Promise<Uint8Array>;
  writeBytes(path: string, bytes: Uint8Array): Promise<void>;
  /**
   * あれば大きな成果物を断片ごとにディスクへ流せる（fetchTransport）。無い FS ではメモリに溜めて writeBytes する。
   * appendBytes は expectedSize を渡すと、ファイルがその長さでないとき失敗する（再開・重複送信で壊さない）。返り値は新しい長さ
   */
  appendBytes?(path: string, bytes: Uint8Array, expectedSize?: number): Promise<number>;
  /** dest が既にあれば置き換える（.part を完成させる） */
  rename?(src: string, dest: string): Promise<void>;
  copyFile(src: string, dest: string): Promise<void>;
  ensureDir(path: string): Promise<void>;
  /** 直下の名前の一覧（無ければ空） */
  listDir(path: string): Promise<string[]>;
  /** ファイルもフォルダも（再帰的に）消す。無ければ何もしない */
  remove(path: string): Promise<void>;
  moveToTrash(path: string): Promise<void>;
  /** zip（store / deflate）を destDir に展開 */
  unzip(zipPath: string, destDir: string): Promise<void>;
  toUrl(path: string): string;
}
