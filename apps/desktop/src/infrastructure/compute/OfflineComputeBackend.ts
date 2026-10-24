// サーバー無し（ADR-0009）。サンプルプロジェクト・未接続のとき。health は失敗、ジョブ系は OFFLINE

import type { ComputeBackendPort } from "@/application/ports/compute";
import { AppError } from "@/domain/errors";
import type { JobEvent } from "@/domain/job";

const offline = (): never => {
  throw new AppError("OFFLINE", "GPU サーバーに接続していません");
};

export class OfflineComputeBackend implements ComputeBackendPort {
  readonly profileId = null;
  health = async () => offline();
  capabilities = async () => offline();
  createProject = async () => offline();
  getProject = async () => offline();
  deleteProject = async () => offline();
  createUpload = async () => offline();
  uploadChunk = async () => offline();
  getUpload = async () => offline();
  completeUpload = async () => offline();
  registerAvatar = async () => offline();
  hasAvatar = async () => offline();
  submitJob = async () => offline();
  getJob = async () => offline();
  listJobs = async () => offline();
  queue = async () => offline();
  cancelJob = async () => offline();
  // eslint-disable-next-line require-yield
  async *events(): AsyncGenerator<JobEvent> {
    offline();
  }
  listArtifacts = async () => offline();
  downloadArtifact = async () => offline();
  logs = async () => offline();
}
