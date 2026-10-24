/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BACKEND?: "mock" | "http" | "memory";
  readonly VITE_SIM_SPEED?: string;
  readonly VITE_SIM_FAIL?: string;
  /** 開発ビルドで最初に開く画面（例 /dev/editor?fixture=v0） */
  readonly VITE_START_ROUTE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare const __APP_VERSION__: string;
/** 合成フィクスチャ v0 の絶対パス（開発用エディタ） */
declare const __FIXTURE_V0__: string;
