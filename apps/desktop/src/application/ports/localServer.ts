// この PC の GPU サーバーの子プロセス（F03 P1）。Tauri は Rust の server_*、ブラウザでは使えない

export interface LocalServerStatus {
  running: boolean;
  pid: number | null;
  /** 終了したときのコード（動いている・未起動なら null） */
  exitCode: number | null;
  /** 標準出力・標準エラーの最近の行 */
  log: string[];
}

export interface LocalServerPort {
  /** 起動する（動いていれば何もしない）。返り値は PID */
  start(input: { serverDir: string; pythonPath: string; port: number; token: string }): Promise<number>;
  /** プロセスツリーごと止める */
  stop(): Promise<void>;
  status(): Promise<LocalServerStatus>;
}
