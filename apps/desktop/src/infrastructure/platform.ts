// 実行環境の判定

import type { LocalInfo } from "./local/localApi";
import { fetchLocalInfo } from "./local/localApi";

/** tauri：現行のデスクトップ版（#29・#40 で消える）／full：ローカルアプリサーバーが画面を配信している／restricted：Pages など、ローカルサーバーなし */
export type RuntimeMode = "tauri" | "full" | "restricted";

export interface Runtime {
  mode: RuntimeMode;
  /** full のとき：サーバーの情報（トークン・許可フォルダ） */
  local?: LocalInfo;
}

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function isMac(): boolean {
  return typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
}

/** 起動時に 1 回。同一オリジンの GET /v1/local/info が答えれば full、それ以外は restricted（features/mode-matrix.md） */
export async function detectRuntime(): Promise<Runtime> {
  if (isTauri()) return { mode: "tauri" };
  const local = await fetchLocalInfo();
  return local ? { mode: "full", local } : { mode: "restricted" };
}
