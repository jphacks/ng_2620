// ComputeBackendPort：推論の実行先（ローカル / リモート / モック / オフライン）を抽象化する中心のポート（F03、ADR-0002）

import type { ComputeProfile } from "@/domain/connection";
import type { Artifact, Job, JobEvent, JobParams, JobStatus, JobType } from "@/domain/job";
import type { MediaMeta } from "@/domain/media";
import type { MediaLimits } from "@/domain/validation";

export interface Health {
  status: "ok";
  version: string;
  apiVersion: number;
}

/** GET /v1/capabilities を正規化したもの */
export interface Capabilities {
  gpuName: string | null;
  vramMb: number | null;
  limits: Partial<MediaLimits> & { maxTargets?: number };
  queueLength: number;
}

/** GET /v1/projects/{id}（再接続時の復元） */
export interface ServerProject {
  projectId: string;
  mediaIds: string[];
  jobs: Job[];
}

export interface CreateUploadReq {
  projectId: string;
  kind: "source";
  filename: string;
  size: number;
  sha256: string;
}

export interface QueueItem {
  jobId: string;
  projectId: string;
  type: JobType;
  status: JobStatus;
  position: number;
}

/**
 * 送る断片。ファイルの範囲で渡すと、実装は WebView を通さずに送れる（Tauri では Rust 側が読む）
 */
export type ChunkBody = Uint8Array | { path: string; offset: number; length: number };

export type DownloadProgress = (received: number, total: number | null) => void;

export interface ComputeBackendPort {
  /** 表示・記録用。どのプロファイルの実装か（オフラインは null） */
  readonly profileId: string | null;

  health(): Promise<Health>;
  capabilities(): Promise<Capabilities>;

  createProject(name: string): Promise<{ projectId: string }>;
  getProject(projectId: string): Promise<ServerProject>;
  deleteProject(projectId: string): Promise<void>;

  createUpload(req: CreateUploadReq): Promise<{ uploadId: string; chunkSize: number }>;
  uploadChunk(uploadId: string, index: number, body: ChunkBody): Promise<void>;
  getUpload(uploadId: string): Promise<{ receivedChunks: number[]; size: number }>;
  completeUpload(uploadId: string): Promise<{ mediaId: string; meta: MediaMeta }>;

  registerAvatar(file: { name: string; bytes: Uint8Array }): Promise<{ avatarId: string }>;
  hasAvatar(avatarId: string): Promise<boolean>;

  submitJob(projectId: string, type: JobType, params: JobParams): Promise<Job>;
  getJob(jobId: string): Promise<Job>;
  listJobs(projectId: string): Promise<Job[]>;
  queue(): Promise<QueueItem[]>;
  cancelJob(jobId: string): Promise<void>;
  /** SSE。切断時は Last-Event-ID で再開する。終了イベント（job_finished / job_failed）の後で終わる */
  events(jobId: string, opts?: { lastEventId?: string; signal?: AbortSignal }): AsyncIterable<JobEvent>;
  listArtifacts(jobId: string): Promise<Artifact[]>;
  /** 成果物をディスクの dest に保存する（Tauri は Rust 側で Range 再開） */
  downloadArtifact(jobId: string, name: string, dest: string, onProgress?: DownloadProgress): Promise<void>;
  logs(jobId: string, tail?: number): Promise<string>;
}

export interface ComputeBackendFactory {
  /** token を渡すと保存済みのトークンの代わりに使う（未保存のプロファイルの接続テスト） */
  forProfile(profile: ComputeProfile, opts?: { token?: string | null }): ComputeBackendPort;
  /** サーバー無し（サンプルプロジェクト・未接続）。ジョブ系は OFFLINE エラー */
  offline(): ComputeBackendPort;
}
