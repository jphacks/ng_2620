// 重みの取得（フルモードだけ。サーバー側が取得する）

import type { WeightsJob, WeightsStatus } from "@/domain/weights";

export interface WeightsPort {
  status(): Promise<WeightsStatus>;
  /** 取得（途中のものは再開）を始める。トークンは送るだけで、保存しない */
  start(hfToken?: string): Promise<WeightsJob>;
  cancel(): Promise<void>;
  /** ジョブの状態を終わりまで流す（signal で打ち切り） */
  watch(onJob: (job: WeightsJob) => void, signal?: AbortSignal): Promise<WeightsJob | null>;
}
