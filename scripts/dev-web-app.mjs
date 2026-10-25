#!/usr/bin/env node
// 画面（Web）の UI・操作感をデバッグするための起動スクリプト。サーバーと画面を一緒に立ち上げ、止めるときも一緒に止める。
//
//   pnpm dev:web              制限モード（サーバーなし。Pages で開いた状態）。Vite の開発サーバー http://localhost:1420/
//   pnpm dev:web:app          フルモード（ソフトウェア同梱版と同じ）。ローカルアプリサーバー + Vite（HMR あり）
//   pnpm dev:web:app:fresh    同上。ただしデータの置き場を空の一時フォルダにする（初回セットアップの案内・空のホームの確認用）
//   pnpm dev:web:app:dist     画面をビルドして、ローカルアプリサーバーが配信する（配布物と同じ。HMR なし）
//
// 環境変数: VVLOG_DEV_PORT（サーバーのポート。既定 8765）、VVLOG_DEV_PYTHON（サーバーを動かす Python）

import { spawn } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const gpu = join(root, "services", "gpu_server");
const flags = new Set(process.argv.slice(2));
const dist = flags.has("--dist");
const fresh = flags.has("--fresh");
const port = Number(process.env.VVLOG_DEV_PORT || 8765);
const win = process.platform === "win32";

function python() {
  if (process.env.VVLOG_DEV_PYTHON) return process.env.VVLOG_DEV_PYTHON;
  const venv = win ? join(gpu, ".venv", "Scripts", "python.exe") : join(gpu, ".venv", "bin", "python");
  return existsSync(venv) ? venv : win ? "python" : "python3";
}

const children = [];
function run(cmd, args, opts = {}) {
  // POSIX ではプロセスグループにして、pnpm の下の vite まで止められるようにする
  const p = spawn(cmd, args, { stdio: "inherit", shell: win && cmd === "pnpm", detached: !win, ...opts });
  children.push(p);
  return p;
}
function stopAll() {
  for (const p of children) {
    if (p.exitCode !== null) continue;
    try {
      if (win) p.kill();
      else process.kill(-p.pid);
    } catch {
      // すでに終わっている
    }
  }
}
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => (stopAll(), process.exit(0)));
process.on("exit", stopAll);

async function waitReady(url, ms = 30_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // まだ起動中
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(
    `サーバーが起動しませんでした: ${url}（Python 環境は services/gpu_server/README.md を参照。VVLOG_DEV_PYTHON で指定できます）`,
  );
}

if (dist) {
  console.log("[dev:web] 画面をビルドします…");
  const code = await new Promise((r) =>
    run("pnpm", ["--filter", "@vvlog/desktop", "build"], { cwd: root }).on("exit", r),
  );
  if (code !== 0) process.exit(code ?? 1);
}

const env = { ...process.env, VVLOG_LOCAL: "1", VVLOG_PORT: String(port) };
if (fresh) {
  const base = join(root, ".dev-data");
  for (const [k, d] of [
    ["VVLOG_LOCAL_PROJECTS_DIR", "projects"],
    ["VVLOG_LOCAL_APPDATA_DIR", "appdata"],
    ["VVLOG_LOCAL_CONFIG_DIR", "config"],
  ]) {
    mkdirSync(join(base, d), { recursive: true });
    env[k] = join(base, d);
  }
  console.log(`[dev:web] データの置き場: ${base}（消せば初回に戻ります）`);
}

const server = run(python(), ["-m", "vvlog_server"], { cwd: gpu, env });
server.on("exit", (c) => {
  console.error(`[dev:web] サーバーが終了しました（コード ${c}）`);
  stopAll();
  process.exit(c ?? 1);
});
await waitReady(`http://127.0.0.1:${port}/v1/local/info`);

if (dist) {
  console.log(`\n[dev:web] ブラウザで http://127.0.0.1:${port}/ を開いてください（Ctrl+C で終了）`);
} else {
  console.log(
    `\n[dev:web] ブラウザで http://localhost:1420/ を開いてください（サーバー: http://127.0.0.1:${port}/、Ctrl+C で終了）`,
  );
  run("pnpm", ["--filter", "@vvlog/desktop", "dev"], {
    cwd: root,
    env: { ...process.env, VVLOG_DEV_SERVER: `http://127.0.0.1:${port}` },
  }).on("exit", (c) => (stopAll(), process.exit(c ?? 0)));
}
