// 解析環境の初回セットアップ（#32）。サーバーの /v1/local/bootstrap が返す状態

export type BootstrapJobState = "idle" | "running" | "succeeded" | "failed" | "canceled";
export type BootstrapStepState = "running" | "done" | "skipped";

export interface BootstrapStep {
  id: string;
  label: string;
  done: boolean;
}

export interface BootstrapJob {
  state: BootstrapJobState;
  step?: string | null;
  steps?: Record<string, BootstrapStepState>;
  log?: string[];
  error?: { code: string; message: string; hint?: string | null; step?: string | null } | null;
}

export interface BootstrapStatus {
  ready: boolean;
  /** GPU の判定。unsupported ならこの PC では環境を作れない */
  judgement: { verdict: "supported" | "unsupported" | "unknown"; reasons: string[]; warnings: string[] };
  steps: BootstrapStep[];
  job: BootstrapJob;
}

export const isBootstrapRunning = (j: BootstrapJob) => j.state === "running";

/** 全体の進み具合 0..1（終わった手順の割合）。分からなければ null */
export function bootstrapFraction(steps: BootstrapStep[], job: BootstrapJob): number | null {
  if (job.state === "succeeded") return 1;
  if (!steps.length) return null;
  const done = steps.filter((s) => s.done || job.steps?.[s.id] === "done" || job.steps?.[s.id] === "skipped").length;
  return done / steps.length;
}
