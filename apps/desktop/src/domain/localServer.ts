// この PC の GPU サーバー（F03 P1、ADR-0002）。アプリが services/gpu_server を子プロセスとして起動・停止する

import type { ComputeProfile } from "./connection";

export interface LocalServerConfig {
  /** services/gpu_server のフォルダ（.venv を含む） */
  serverDir: string;
  /** 推論（pipeline/）を動かす Python。VVLOG_PYTHON として渡す */
  pythonPath: string;
  port: number;
  /** アプリの起動時に一緒に起動する */
  autoStart: boolean;
}

/** 起動したサーバーにつなぐ接続プロファイルの ID（トークンも同じキーで保存） */
export const LOCAL_PROFILE_ID = "local-gpu";
export const DEFAULT_LOCAL_PORT = 8765;
/** 起動してから health が返るまで待つ時間 */
export const LOCAL_START_TIMEOUT_MS = 60_000;

export function defaultLocalServerConfig(): LocalServerConfig {
  return { serverDir: "", pythonPath: "", port: DEFAULT_LOCAL_PORT, autoStart: false };
}

export function localProfile(port: number, name: string): ComputeProfile {
  return { id: LOCAL_PROFILE_ID, name, kind: "local", baseUrl: `http://127.0.0.1:${port}`, tokenRef: LOCAL_PROFILE_ID };
}

export type LocalServerProblem = "server_dir_required" | "python_required" | "port_invalid";

export function validateLocalServer(c: LocalServerConfig): LocalServerProblem[] {
  const out: LocalServerProblem[] = [];
  if (!c.serverDir.trim()) out.push("server_dir_required");
  if (!c.pythonPath.trim()) out.push("python_required");
  if (!Number.isInteger(c.port) || c.port < 1 || c.port > 65535) out.push("port_invalid");
  return out;
}
