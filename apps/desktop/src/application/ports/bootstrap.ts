// 解析環境の初回セットアップ（フルモードだけ。サーバー側が uv で作る）

import type { BootstrapJob, BootstrapStatus } from "@/domain/bootstrap";

export interface BootstrapPort {
  status(): Promise<BootstrapStatus>;
  /** 作成（途中のものは再開）を始める */
  start(): Promise<BootstrapJob>;
  cancel(): Promise<void>;
  /** ジョブの状態を終わりまで流す（signal で打ち切り） */
  watch(onJob: (job: BootstrapJob) => void, signal?: AbortSignal): Promise<BootstrapJob | null>;
}
