// フルモードのドロップ取り込み（#47、#28 の決定）。ブラウザの drop は File でパスが無いので、
// ローカルサーバーの取り込みフォルダへ分割して書き（巨大な動画でも丸ごとメモリに載せない）、以後はサーバー上のパスで扱う。
// 受け付けない先（拡張子が違うなど）には何も送らない：drop イベントは取り込み先のパスだけ持ち、prepare() で初めて送る

import type { DropUploadEvent, FileDropEvent, FileDropPort } from "@/application/ports/system";
import type { FileSystem } from "../fs/FileSystem";
import { basenameOf, joinPath } from "../fs/path";

export const UPLOAD_CHUNK = 8 * 1024 * 1024;
/** 取り込みフォルダの掃除：どのプロジェクトからも使われていない、これより古い取り込みを消す */
export const IMPORT_MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** パスに使えない文字を除く。空になったら代わりの名前 */
export function safeFileName(name: string): string {
  const base = [...basenameOf(name)]
    .map((c) => (c.charCodeAt(0) < 32 || '<>:"|?*\\/'.includes(c) ? "_" : c))
    .join("")
    .replace(/^\.+/, "")
    .trim();
  return base || "video";
}

/** File を dest へ。<dest>.part に追記して最後に rename（途中のものが dest として見えない）。進捗は断片ごと */
export async function uploadFileToServer(
  file: Blob,
  dest: string,
  fs: Required<Pick<FileSystem, "appendBytes" | "rename">> & Pick<FileSystem, "remove" | "ensureDir">,
  opts: { chunkSize?: number; signal?: AbortSignal; onProgress?: (sent: number, total: number) => void } = {},
): Promise<void> {
  const { chunkSize = UPLOAD_CHUNK, signal, onProgress } = opts;
  const part = `${dest}.part`;
  let sent = 0;
  try {
    await fs.remove(part); // 同じ場所の古い断片は使わない（毎回新しい取り込み先なので通常は無い）
    do {
      signal?.throwIfAborted();
      const bytes = new Uint8Array(await file.slice(sent, sent + chunkSize).arrayBuffer());
      sent = await fs.appendBytes(part, bytes, sent);
      onProgress?.(sent, file.size);
    } while (sent < file.size);
    signal?.throwIfAborted();
    await fs.rename(part, dest);
  } catch (e) {
    await fs.remove(part).catch(() => {});
    throw e;
  }
}

export interface ServerDropDeps {
  fs: FileSystem;
  /** 取り込みフォルダ（許可フォルダの中。例 <appData>/imports） */
  importsDir: () => Promise<string>;
  newId: () => string;
  onUpload: (ev: DropUploadEvent) => void;
}

export function serverFileDrop(deps: ServerDropDeps): FileDropPort {
  return {
    subscribe(listener: (ev: FileDropEvent) => void) {
      const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
      const over = (e: DragEvent) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        listener({ type: "over", x: e.clientX, y: e.clientY, paths: [] });
      };
      const leave = (e: DragEvent) => {
        if (e.relatedTarget === null) listener({ type: "leave" });
      };
      const drop = (e: DragEvent) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        const files = Array.from(e.dataTransfer?.files ?? []);
        // 取り込み先は File ごとに決まっている（フォルダ名は取り込みごとの ID）。送るのは prepare() が呼ばれてから
        const group = deps.newId();
        const plan = files.map((file) => ({ file, id: deps.newId(), name: safeFileName(file.name) }));
        const destOf = async (name: string) => joinPath(await deps.importsDir(), group, name);
        void Promise.all(plan.map((p) => destOf(p.name))).then((paths) => {
          listener({
            type: "drop",
            x: e.clientX,
            y: e.clientY,
            paths,
            prepare: async () => {
              const done: string[] = [];
              for (const [i, p] of plan.entries()) {
                const ok = await receive(deps, p, paths[i]!);
                if (ok) done.push(paths[i]!);
              }
              return done;
            },
          });
        });
      };
      window.addEventListener("dragover", over);
      window.addEventListener("dragleave", leave);
      window.addEventListener("drop", drop);
      return () => {
        window.removeEventListener("dragover", over);
        window.removeEventListener("dragleave", leave);
        window.removeEventListener("drop", drop);
      };
    },
  };
}

async function receive(
  deps: ServerDropDeps,
  p: { file: File; id: string; name: string },
  dest: string,
): Promise<boolean> {
  const ctl = new AbortController();
  const ev: DropUploadEvent = {
    id: p.id,
    name: p.name,
    state: "uploading",
    sent: 0,
    total: p.file.size,
    cancel: () => ctl.abort(),
  };
  deps.onUpload({ ...ev });
  if (!deps.fs.appendBytes || !deps.fs.rename) {
    deps.onUpload({ ...ev, state: "error", cancel: undefined, error: "この環境ではサーバーへ取り込めません" });
    return false;
  }
  try {
    await uploadFileToServer(p.file, dest, deps.fs as Parameters<typeof uploadFileToServer>[2], {
      signal: ctl.signal,
      onProgress: (sent) => deps.onUpload({ ...ev, sent }),
    });
    deps.onUpload({ ...ev, state: "done", sent: p.file.size, cancel: undefined });
    return true;
  } catch (e) {
    if (ctl.signal.aborted) deps.onUpload({ ...ev, state: "canceled", cancel: undefined });
    else deps.onUpload({ ...ev, state: "error", cancel: undefined, error: e instanceof Error ? e.message : String(e) });
    return false;
  }
}

/**
 * 取り込みフォルダの掃除。referenced は、プロジェクトが元の動画として参照しているパス。
 * どれからも参照されず、中のファイルがすべて maxAge より古い取り込みを消す。消した数を返す
 */
export async function sweepImports(
  fs: FileSystem,
  importsDir: string,
  referenced: Iterable<string>,
  now = Date.now(),
  maxAgeMs = IMPORT_MAX_AGE_MS,
): Promise<number> {
  // 区切りは / に揃え、ドライブ文字のあるパス（Windows）は大文字小文字を無視して比べる
  const key = (p: string) => {
    const n = p.replace(/\\/g, "/");
    return /^[a-z]:/i.test(n) ? n.toLowerCase() : n;
  };
  const refs = [...referenced].map(key);
  let removed = 0;
  for (const group of await fs.listDir(importsDir)) {
    const dir = joinPath(importsDir, group);
    const d = key(dir);
    if (refs.some((r) => r === d || r.startsWith(`${d}/`))) continue;
    try {
      const names = await fs.listDir(dir);
      let newest = 0;
      for (const n of names) newest = Math.max(newest, (await fs.stat(joinPath(dir, n))).mtimeMs);
      if (now - newest < maxAgeMs && names.length > 0) continue;
      await fs.remove(dir);
      removed++;
    } catch {
      // 掃除の失敗で起動を止めない
    }
  }
  return removed;
}
