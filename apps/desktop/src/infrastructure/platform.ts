// 実行環境の判定

import type { LocalInfo } from "./local/localApi";
import { PersistentFs } from "./fs/persistentFs";
import { fetchLocalInfo } from "./local/localApi";

/** full：ローカルアプリサーバーが画面を配信している／restricted：Pages など、ローカルサーバーなし */
export type RuntimeMode = "full" | "restricted";

export interface Runtime {
  mode: RuntimeMode;
  /** full のとき：サーバーの情報（トークン・許可フォルダ） */
  local?: LocalInfo;
  /** restricted のとき：IndexedDB に残る保存領域。使えない環境（プライベートモードなど）では無い＝メモリだけ */
  storage?: PersistentFs;
}

export function isMac(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
}

/** 制限モードの保存領域を開く。ブラウザに消されにくくなるよう永続化も頼む（許可されなくても動く） */
export async function openRestrictedStorage(): Promise<PersistentFs | undefined> {
  if (typeof indexedDB === "undefined") return undefined;
  try {
    const fs = await PersistentFs.open();
    void navigator.storage?.persist?.().catch(() => {});
    return fs;
  } catch (e) {
    console.warn("[storage] IndexedDB を開けないので、メモリ上だけで動きます", e);
    return undefined;
  }
}

/** 起動時に 1 回。同一オリジンの GET /v1/local/info が答えれば full、それ以外は restricted（features/mode-matrix.md） */
export async function detectRuntime(): Promise<Runtime> {
  const local = await fetchLocalInfo();
  return local ? { mode: "full", local } : { mode: "restricted", storage: await openRestrictedStorage() };
}
