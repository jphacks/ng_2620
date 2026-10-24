/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BACKEND?: "mock" | "http" | "memory";
  readonly VITE_SIM_SPEED?: string;
  readonly VITE_SIM_FAIL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare const __APP_VERSION__: string;
