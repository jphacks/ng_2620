// 制限モード（Pages 単体）のファイル置き場：IndexedDB に残る MemoryFs（#27）。
// 全部のエントリの Blob の『持ち手』を起動時に読み込み（中身はブラウザがディスクから遅延で読む）、読み書きはメモリ上で同期的に行い、
// 書き込みは IndexedDB へも書いてから返す。toUrl が同期で返せるのはこのため。
// Safari は一定期間操作が無いと IndexedDB を消すことがある（ITP）ので、重要なものは zip で書き出して残す前提（#28）

import { AppError } from "@/domain/errors";
import { MemoryFs, norm } from "./memoryFs";

const STORE = "entries";

interface Row {
  p: string;
  blob?: Blob;
  dir?: true;
  mtimeMs: number;
}

function req<T>(r: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

function toSaveError(e: unknown): AppError {
  const name = (e as { name?: string } | null)?.name;
  if (name === "QuotaExceededError") {
    return new AppError(
      "SAVE_FAILED",
      "ブラウザの保存容量が足りません。不要なプロジェクトを消すか、zip で書き出してから消してください",
    );
  }
  return new AppError(
    "SAVE_FAILED",
    `ブラウザの保存領域に書けませんでした: ${e instanceof Error ? e.message : String(e)}`,
  );
}

export class PersistentFs extends MemoryFs {
  private chain: Promise<void> = Promise.resolve();
  private failure: unknown = null;

  private constructor(private readonly db: IDBDatabase) {
    super();
  }

  /** 開いて、保存済みのエントリを読み込む */
  static async open(name = "vvlog-fs", idb: IDBFactory = indexedDB): Promise<PersistentFs> {
    const opening = idb.open(name, 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore(STORE, { keyPath: "p" });
    const fs = new PersistentFs(await req(opening));
    const rows = await req<Row[]>(fs.db.transaction(STORE).objectStore(STORE).getAll());
    for (const r of rows) {
      if (r.dir) fs.dirs.add(r.p);
      else if (r.blob) fs.files.set(r.p, { blob: r.blob, mtimeMs: r.mtimeMs });
    }
    return fs;
  }

  close() {
    this.db.close();
  }

  /** 保存済みの量（目安）。navigator.storage.estimate() が使えなければ null */
  static async usage(): Promise<{ usage: number; quota: number } | null> {
    const est = await navigator.storage?.estimate?.();
    return est?.usage !== undefined && est.quota !== undefined ? { usage: est.usage, quota: est.quota } : null;
  }

  // --- 書き込みを IndexedDB にも反映する ---------------------------------------------------------------------------------
  private persist(work: (store: IDBObjectStore) => void) {
    this.chain = this.chain.then(async () => {
      try {
        const tx = this.db.transaction(STORE, "readwrite");
        work(tx.objectStore(STORE));
        await done(tx);
      } catch (e) {
        this.failure = e;
      }
    });
  }

  /** ここまでの書き込みが IndexedDB に入るのを待つ。入らなかったら SAVE_FAILED */
  private async settle() {
    await this.chain;
    if (this.failure) {
      const e = this.failure;
      this.failure = null;
      throw toSaveError(e);
    }
  }

  protected override setBlob(path: string, blob: Blob) {
    super.setBlob(path, blob);
    const p = norm(path);
    const mtimeMs = this.files.get(p)!.mtimeMs;
    this.persist((s) => void s.put({ p, blob, mtimeMs } satisfies Row));
  }

  override async writeTextAtomic(path: string, text: string) {
    await super.writeTextAtomic(path, text);
    await this.settle();
  }
  override async writeBytes(path: string, bytes: Uint8Array) {
    await super.writeBytes(path, bytes);
    await this.settle();
  }
  override async appendBytes(path: string, bytes: Uint8Array, expectedSize?: number) {
    const n = await super.appendBytes(path, bytes, expectedSize);
    await this.settle();
    return n;
  }
  override async rename(src: string, dest: string) {
    await super.rename(src, dest);
    await this.settle();
  }
  override async copyFile(src: string, dest: string) {
    await super.copyFile(src, dest);
    await this.settle();
  }
  override async unzip(zipPath: string, destDir: string) {
    await super.unzip(zipPath, destDir);
    await this.settle();
  }
  override async ensureDir(path: string) {
    await super.ensureDir(path);
    const p = norm(path);
    this.persist((s) => void s.put({ p, dir: true, mtimeMs: Date.now() } satisfies Row));
    await this.settle();
  }
  override async remove(path: string) {
    const p = norm(path);
    const gone = [...this.files.keys(), ...this.dirs].filter((k) => k === p || k.startsWith(`${p}/`));
    await super.remove(path);
    if (gone.length > 0) this.persist((s) => gone.forEach((k) => s.delete(k)));
    await this.settle();
  }
}
