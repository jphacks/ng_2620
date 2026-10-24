// LocalServerPort の実装。Tauri は Rust の server_start / server_stop / server_status、ブラウザでは使えない

import { invoke } from "@tauri-apps/api/core";
import type { LocalServerPort, LocalServerStatus } from "@/application/ports/localServer";
import { AppError } from "@/domain/errors";

export const tauriLocalServer: LocalServerPort = {
  async start(config) {
    try {
      return await invoke<number>("server_start", { config });
    } catch (e) {
      throw new AppError("LOCAL_SERVER_FAILED", String(e));
    }
  },
  stop: () => invoke<void>("server_stop"),
  status: () => invoke<LocalServerStatus>("server_status"),
};

export const browserLocalServer: LocalServerPort = {
  async start() {
    throw new AppError("NOT_IMPLEMENTED", "ブラウザ版ではサーバーを起動できません");
  },
  async stop() {},
  async status() {
    return { running: false, pid: null, exitCode: null, log: [] };
  },
};
