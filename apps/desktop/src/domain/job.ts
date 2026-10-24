// ジョブ・ステージ・イベント（contracts §2.2, §3、ADR-0004）と、イベントから状態を作る純粋関数

import type { ApiErrorBody } from "./errors";
import type { OcclusionOverride } from "./overrides";
import { STAGE_WEIGHTS } from "./jobWeights";

export type JobType = "analyze" | "render" | "export";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";
export type StageName =
  | "ingest"
  | "track"
  | "mesh"
  | "occlusion" // analyze
  | "removal"
  | "avatar"
  | "layers" // render
  | "composite"
  | "encode"; // export
export type StageStatus = "pending" | "running" | "done" | "failed" | "skipped";

export const STAGES: Record<JobType, readonly StageName[]> = {
  analyze: ["ingest", "track", "mesh", "occlusion"],
  render: ["removal", "avatar", "layers"],
  export: ["composite", "encode"],
};

export interface StageState {
  name: StageName;
  status: StageStatus;
  progress: number; // 0..1
  etaSec?: number;
  startedAt?: string;
  finishedAt?: string;
  /** 以下はアプリ側だけで持つ（progress.message / stage_finished.durationSec） */
  message?: string;
  durationSec?: number;
}

export interface AnalyzeParams {
  mediaId: string;
  maxTargets?: number; // 既定 4、上限 4
}

export interface RenderParams {
  analyzeJobId: string;
  cast: { personId: number; action: "replace"; avatarId: string }[];
  finish?: { smooth?: number; footLock?: boolean; spring?: boolean };
}

export type ExportFormat = "mp4_h264";
export interface ExportSettings {
  format: ExportFormat; // 将来 "prores4444" | "png_seq" | "layers" | "vrma"（予約、501）
  crf?: number;
  audio?: boolean;
  resolution?: "full" | "proxy";
}

export interface ExportParams {
  renderJobId: string;
  overrides: OcclusionOverride[];
  settings: ExportSettings;
}

export type JobParams = AnalyzeParams | RenderParams | ExportParams;

export type ArtifactKind = "json" | "zip" | "video" | "image";

export interface Artifact {
  name: string;
  kind: ArtifactKind;
  stage: StageName;
  size: number;
  sha256: string;
  contentType: string;
  url: string; // /v1/jobs/{jobId}/artifacts/{name}
}

export interface Job {
  jobId: string;
  projectId: string;
  type: JobType;
  status: JobStatus;
  createdAt: string;
  startedAt?: string;
  finishedAt?: string;
  queuePosition?: number; // queued のときだけ（0 = 次に実行）
  dependsOn?: string;
  stages: StageState[];
  artifacts: string[]; // 公開済みの成果物名
  error?: ApiErrorBody;
  params: JobParams;
}

// --- SSE イベント（contracts §3.3） ---

interface EventBase {
  /** SSE の `id:`。Last-Event-ID の再開に使う */
  id?: string;
}

export type JobEvent = EventBase &
  (
    | { type: "snapshot"; data: Job }
    | { type: "queued"; data: { position: number } }
    | { type: "stage_started"; data: { stage: StageName } }
    | { type: "progress"; data: { stage: StageName; progress: number; etaSec?: number; message?: string } }
    | { type: "artifact_ready"; data: Artifact }
    | { type: "stage_finished"; data: { stage: StageName; durationSec: number } }
    | { type: "job_finished"; data: { status: "succeeded" | "canceled" } }
    | { type: "job_failed"; data: { stage: StageName; error: { code: string; message: string }; logTail: string } }
    | { type: "heartbeat"; data: Record<string, never> }
  );

export type JobEventType = JobEvent["type"];

export const JOB_EVENT_TYPES: readonly JobEventType[] = [
  "snapshot",
  "queued",
  "stage_started",
  "progress",
  "artifact_ready",
  "stage_finished",
  "job_finished",
  "job_failed",
  "heartbeat",
];

export function isTerminal(status: JobStatus): boolean {
  return status === "succeeded" || status === "failed" || status === "canceled";
}

function isStageClosed(s: StageState): boolean {
  return s.status === "done" || s.status === "failed" || s.status === "skipped";
}

function clamp01(x: number): number {
  return Number.isFinite(x) ? Math.min(1, Math.max(0, x)) : 0;
}

function updateStage(job: Job, name: StageName, fn: (s: StageState) => StageState): Job {
  let found = false;
  const stages = job.stages.map((s) => {
    if (s.name !== name) return s;
    found = true;
    return fn(s);
  });
  if (!found) stages.push(fn({ name, status: "pending", progress: 0 }));
  return { ...job, stages };
}

/**
 * イベントを 1 件適用した新しい Job を返す（純粋関数）。
 * - `snapshot` は全置換。
 * - 再送・順不同に耐える：終了済みのジョブ・ステージは古いイベントで巻き戻さない。進捗は減らさない。
 */
export function applyEvent(job: Job, ev: JobEvent, now?: string): Job {
  if (ev.type === "snapshot") return ev.data;
  if (ev.type === "heartbeat") return job;

  if (ev.type === "artifact_ready") {
    return job.artifacts.includes(ev.data.name) ? job : { ...job, artifacts: [...job.artifacts, ev.data.name] };
  }
  if (isTerminal(job.status)) return job;

  switch (ev.type) {
    case "queued":
      return { ...job, status: "queued", queuePosition: ev.data.position };

    case "stage_started": {
      const started = { ...job, status: "running" as const, queuePosition: undefined, startedAt: job.startedAt ?? now };
      return updateStage(started, ev.data.stage, (s) =>
        isStageClosed(s) ? s : { ...s, status: "running", startedAt: s.startedAt ?? now },
      );
    }

    case "progress": {
      const running = { ...job, status: "running" as const, queuePosition: undefined, startedAt: job.startedAt ?? now };
      return updateStage(running, ev.data.stage, (s) =>
        isStageClosed(s)
          ? s
          : {
              ...s,
              status: "running",
              progress: Math.max(s.progress, clamp01(ev.data.progress)),
              etaSec: ev.data.etaSec,
              message: ev.data.message ?? s.message,
              startedAt: s.startedAt ?? now,
            },
      );
    }

    case "stage_finished":
      return updateStage(job, ev.data.stage, (s) =>
        s.status === "failed"
          ? s
          : {
              ...s,
              status: "done",
              progress: 1,
              etaSec: undefined,
              durationSec: ev.data.durationSec,
              finishedAt: s.finishedAt ?? now,
            },
      );

    case "job_finished": {
      const finished: Job = {
        ...job,
        status: ev.data.status,
        finishedAt: job.finishedAt ?? now,
        queuePosition: undefined,
      };
      if (ev.data.status !== "succeeded") return finished;
      return {
        ...finished,
        stages: finished.stages.map((s) =>
          s.status === "pending" || s.status === "running"
            ? { ...s, status: "done", progress: 1, etaSec: undefined }
            : s,
        ),
      };
    }

    case "job_failed": {
      const failed: Job = {
        ...job,
        status: "failed",
        finishedAt: job.finishedAt ?? now,
        queuePosition: undefined,
        error: {
          code: ev.data.error.code,
          message: ev.data.error.message,
          detail: { stage: ev.data.stage, logTail: ev.data.logTail },
        },
      };
      return updateStage(failed, ev.data.stage, (s) => ({ ...s, status: "failed", etaSec: undefined }));
    }
  }
}

/** ステージの重み付きでジョブ全体の進捗 0..1 */
export function overallProgress(job: Job): number {
  if (job.status === "succeeded") return 1;
  const order = STAGES[job.type];
  const weights = STAGE_WEIGHTS[job.type];
  let total = 0;
  let sum = 0;
  for (const name of order) {
    const w = weights[name] ?? 1 / order.length;
    const s = job.stages.find((x) => x.name === name);
    total += w;
    if (!s) continue;
    sum += w * (s.status === "done" || s.status === "skipped" ? 1 : clamp01(s.progress));
  }
  return total > 0 ? clamp01(sum / total) : 0;
}

/** いま動いている（無ければ次に動く）ステージ */
export function currentStage(job: Job): StageState | null {
  const running = job.stages.find((s) => s.status === "running");
  if (running) return running;
  if (job.status === "failed") return job.stages.find((s) => s.status === "failed") ?? null;
  const order = STAGES[job.type];
  for (const name of order) {
    const s = job.stages.find((x) => x.name === name);
    if (!s || s.status === "pending") return s ?? { name, status: "pending", progress: 0 };
  }
  return job.stages[job.stages.length - 1] ?? null;
}

export function stageState(job: Job | null | undefined, name: StageName): StageState {
  return job?.stages.find((s) => s.name === name) ?? { name, status: "pending", progress: 0 };
}

export function isStageDone(job: Job | null | undefined, name: StageName): boolean {
  const s = stageState(job, name);
  return s.status === "done" || s.status === "skipped";
}

/** ステージを順に並べた一覧（サーバーが未送信のステージは pending で補う） */
export function orderedStages(job: Job): StageState[] {
  return STAGES[job.type].map((name) => stageState(job, name));
}

/** サーバーへ投入する前のジョブの初期形（InMemory 実装や楽観表示に使う） */
export function newJob(input: {
  jobId: string;
  projectId: string;
  type: JobType;
  params: JobParams;
  createdAt: string;
  dependsOn?: string;
}): Job {
  return {
    jobId: input.jobId,
    projectId: input.projectId,
    type: input.type,
    status: "queued",
    createdAt: input.createdAt,
    dependsOn: input.dependsOn,
    stages: STAGES[input.type].map((name) => ({ name, status: "pending", progress: 0 })),
    artifacts: [],
    params: input.params,
  };
}
