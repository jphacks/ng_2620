// 全体進捗を出すときのステージの重み（ジョブごとに合計 1）。実測に合わせて調整してよい

import type { JobType, StageName } from "./job";

export const STAGE_WEIGHTS: Record<JobType, Partial<Record<StageName, number>>> = {
  analyze: { ingest: 0.05, track: 0.35, mesh: 0.35, occlusion: 0.25 },
  render: { removal: 0.3, avatar: 0.5, layers: 0.2 },
  export: { composite: 0.7, encode: 0.3 },
};
