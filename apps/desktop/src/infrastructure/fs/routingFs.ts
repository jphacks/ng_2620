// 制限モードのファイル置き場の振り分け（#27）。
//   volatile    ドロップ・選択した端末のファイル（/mem/drops/…）。File への参照なのでブラウザを閉じると消える。コピーして IndexedDB に入れない
//   persistent  プロジェクト・キャッシュ・アバター・設定。IndexedDB に残る（PersistentFs）
// appendBytes / rename は持たない：fetchTransport は『追記できない FS』としてメモリに溜めて書く（IndexedDB に追記は重い）

import type { FileStat } from "@/application/ports/media";
import { basenameOf } from "./path";
import type { FileSystem } from "./FileSystem";

export class RoutingFs implements FileSystem {
  constructor(
    private readonly persistent: FileSystem,
    private readonly volatile: FileSystem,
    private readonly isVolatile: (path: string) => boolean,
  ) {}

  private of(path: string): FileSystem {
    return this.isVolatile(path) ? this.volatile : this.persistent;
  }

  exists(p: string) {
    return this.of(p).exists(p);
  }
  stat(p: string): Promise<FileStat> {
    return this.of(p).stat(p);
  }
  readText(p: string) {
    return this.of(p).readText(p);
  }
  writeTextAtomic(p: string, text: string, opts?: { private?: boolean }) {
    return this.of(p).writeTextAtomic(p, text, opts);
  }
  readBytes(p: string) {
    return this.of(p).readBytes(p);
  }
  readChunk(p: string, offset: number, length: number) {
    return this.of(p).readChunk(p, offset, length);
  }
  writeBytes(p: string, bytes: Uint8Array) {
    return this.of(p).writeBytes(p, bytes);
  }
  async copyFile(src: string, dest: string) {
    const from = this.of(src);
    const to = this.of(dest);
    if (from === to) return from.copyFile(src, dest);
    await to.writeBytes(dest, await from.readBytes(src));
  }
  ensureDir(p: string) {
    return this.of(p).ensureDir(p);
  }
  listDir(p: string) {
    return this.of(p).listDir(p);
  }
  remove(p: string) {
    return this.of(p).remove(p);
  }
  moveToTrash(p: string) {
    return this.of(p).moveToTrash(p);
  }
  async unzip(zipPath: string, destDir: string) {
    const from = this.of(zipPath);
    const to = this.of(destDir);
    if (from === to) return to.unzip(zipPath, destDir);
    // zip と展開先が別の置き場：展開先の置き場に一時的に zip を置いて展開する
    const tmp = `${destDir}/.unzip-${basenameOf(zipPath)}`;
    await to.writeBytes(tmp, await from.readBytes(zipPath));
    try {
      await to.unzip(tmp, destDir);
    } finally {
      await to.remove(tmp);
    }
  }
  toUrl(p: string) {
    return this.of(p).toUrl(p);
  }
}
