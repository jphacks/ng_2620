// 画面の E2E（#39、#28 の決定：Chromium と WebKit）。モックサーバー（GPU なし・再生）を使い、実ブラウザで主要フローを通す。
//   pnpm e2e                 # 初回: pnpm exec playwright install chromium webkit（Linux は --with-deps）
// 前提: pnpm build 済みの dist/、services/gpu_server/.venv（環境 A）、ffmpeg。VVLOG_E2E_PYTHON で Python を変えられる

import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";
import { PORTS } from "./e2e/ports";

const root = fileURLToPath(new URL("../..", import.meta.url));
const server = `${root}services/gpu_server`;
const python =
  process.env.VVLOG_E2E_PYTHON ??
  [`${server}/.venv/bin/python`, `${server}/.venv/Scripts/python.exe`].find(existsSync) ??
  "python3";
const dist = fileURLToPath(new URL("./dist", import.meta.url));
const data = fileURLToPath(new URL("./e2e/.run", import.meta.url));

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  globalSetup: "./e2e/global-setup.ts",
  use: { trace: "retain-on-failure", screenshot: "only-on-failure", viewport: { width: 1440, height: 900 }, locale: "ja-JP" },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "webkit", use: { ...devices["Desktop Safari"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: [
    {
      // フルモード：ローカルアプリサーバー（画面も配信する）。GPU の代わりに再生用のステージ
      command: `${python} -m mock_server --port ${PORTS.local} --local --spa-dir ${dist}`,
      cwd: `${root}services/mock_server`,
      url: `http://127.0.0.1:${PORTS.local}/v1/health`,
      env: {
        VVLOG_MOCK_SPEED: "60",
        VVLOG_DATA_ROOT: `${data}/local-data`,
        VVLOG_LOCAL_PROJECTS_DIR: `${data}/projects`,
        VVLOG_LOCAL_APPDATA_DIR: `${data}/appdata`,
        VVLOG_LOCAL_CONFIG_DIR: `${data}/config`,
      },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      // 制限モードの相手：自分で用意した外部 GPU（BYO）を模したモックサーバー。Pages 相当の画面のオリジンだけを許可する
      command: `${python} -m mock_server --port ${PORTS.mockGpu}`,
      cwd: `${root}services/mock_server`,
      url: `http://127.0.0.1:${PORTS.mockGpu}/v1/health`,
      env: {
        VVLOG_MOCK_SPEED: "60",
        VVLOG_DATA_ROOT: `${data}/gpu-data`,
        VVLOG_ALLOWED_ORIGINS: `http://127.0.0.1:${PORTS.staticPages}`,
      },
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      // 制限モードの画面：ローカルサーバー無しの静的配信（Pages 相当）。サブパスで配信して相対パスのビルドも確かめる
      command: `${python} ${fileURLToPath(new URL("./e2e/static_server.py", import.meta.url))} ${dist} ${PORTS.staticPages}`,
      url: `http://127.0.0.1:${PORTS.staticPages}/ng_2620/`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
});
