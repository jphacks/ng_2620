import { readFileSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf-8")) as { version: string };

// Tauri 2 の標準設定：固定ポート 1420、Rust 側の変更で再読み込みしない
export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    // 開発用エディタ（/#/dev/editor?fixture=v0）が開く合成フィクスチャ（fixtures/synth/make_synth.py）
    __FIXTURE_V0__: JSON.stringify(fileURLToPath(new URL("../../fixtures/synth/synth.vvlog", import.meta.url))),
  },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: { ignored: ["**/src-tauri/**"] },
    // フルモードを `pnpm dev` で試す：VVLOG_DEV_SERVER=http://127.0.0.1:<port> pnpm dev
    // （サーバーは同一オリジンの呼び出しにだけトークンを渡すので、Host を書き換え Origin を外して転送する）
    proxy: process.env.VVLOG_DEV_SERVER
      ? {
          "/v1": {
            target: process.env.VVLOG_DEV_SERVER,
            changeOrigin: true,
            configure: (proxy) => proxy.on("proxyReq", (req) => req.removeHeader("origin")),
          },
        }
      : undefined,
  },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: {
    target: "safari16",
    sourcemap: true,
    // デスクトップアプリはローカルから読むので 1 ファイルで構わない
    chunkSizeWarningLimit: 2000,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    // Node 25 以降は Node 自身が globalThis.localStorage を持ち（--localstorage-file なしでは使えない）、
    // jsdom の localStorage を隠してしまう。テストのワーカーでは Node の Web Storage を切る
    execArgv: ["--no-experimental-webstorage"],
  },
});
