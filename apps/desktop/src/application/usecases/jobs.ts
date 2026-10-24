// F05 ジョブの投入・購読・成果物の取得・取り消し・再試行・復元

import { AppError, isAppError, toAppError } from "@/domain/errors";
import type { Artifact, Job, JobParams, JobType } from "@/domain/job";
import { applyEvent, isTerminal } from "@/domain/job";
import type { ExportSettings } from "@/domain/job";
import type { Project, ProjectMutation } from "@/domain/project";
import { allJobIds, recordJob, syncJobStatus } from "@/domain/project";
import type { LayerCachePort } from "../ports/cache";
import type { ComputeBackendPort, DownloadProgress, QueueItem } from "../ports/compute";
import type { ProjectRepositoryPort } from "../ports/project";
import type { ClockPort } from "../ports/system";

export interface SubmitExtra {
  castHash?: string;
  overridesHash?: string;
  file?: string;
  settings?: ExportSettings;
}

export class SubmitJob {
  /** 投入して project.jobs に記録する。依存（render → analyze など）はサーバーが待つ */
  async execute(input: {
    project: Project;
    backend: ComputeBackendPort;
    type: JobType;
    params: JobParams;
    extra?: SubmitExtra;
  }): Promise<{ job: Job; apply: ProjectMutation }> {
    const serverProjectId = input.project.compute.serverProjectId;
    if (!serverProjectId) throw new AppError("DEPENDENCY_NOT_READY", "サーバー側のプロジェクトがまだありません");
    const job = await input.backend.submitJob(serverProjectId, input.type, input.params);
    return { job, apply: (p) => recordJob(p, job, input.extra) };
  }
}

export interface SubscribeHandlers {
  onJob(job: Job): void;
  /** 新しく公開された成果物（重複なし）。完了を待たずに次のイベントを処理する */
  onArtifact?(artifact: Artifact, job: Job): Promise<void> | void;
  /** SSE を諦めてポーリングに切り替えた */
  onFallback?(error: AppError): void;
}

export class SubscribeJob {
  constructor(private readonly d: { clock: ClockPort }) {}

  /**
   * SSE を購読して終了まで状態を流す。SSE が使えなければポーリング（GET /v1/jobs/{id}）に落とす。
   * 接続直後の snapshot と終了時に listArtifacts で取りこぼした成果物を補う。
   */
  async execute(input: {
    backend: ComputeBackendPort;
    jobId: string;
    handlers: SubscribeHandlers;
    signal?: AbortSignal;
    pollIntervalMs?: number;
  }): Promise<Job> {
    const { clock } = this.d;
    const { backend, jobId, handlers, signal } = input;
    const now = () => clock.now().toISOString();
    const seen = new Set<string>();
    const pending: Promise<unknown>[] = [];
    let job: Job | null = null;

    const emitArtifact = (a: Artifact, j: Job) => {
      if (seen.has(a.name)) return;
      seen.add(a.name);
      if (handlers.onArtifact) pending.push(Promise.resolve(handlers.onArtifact(a, j)).catch(() => {}));
    };
    const fillArtifacts = async (j: Job) => {
      if (!handlers.onArtifact || j.artifacts.every((n) => seen.has(n))) return;
      const list = await backend.listArtifacts(jobId);
      for (const a of list) if (j.artifacts.includes(a.name) || isTerminal(j.status)) emitArtifact(a, j);
    };

    try {
      for await (const ev of backend.events(jobId, { signal })) {
        if (!job && ev.type !== "snapshot") job = await backend.getJob(jobId);
        job = job ? applyEvent(job, ev, now()) : (ev as { data: Job }).data;
        handlers.onJob(job);
        if (ev.type === "artifact_ready") emitArtifact(ev.data, job);
        if (ev.type === "snapshot") await fillArtifacts(job);
        if (isTerminal(job.status)) break;
      }
    } catch (e) {
      if (signal?.aborted) throw e;
      handlers.onFallback?.(toAppError(e));
    }

    // SSE が終了前に途切れた / 使えない → ポーリング
    let failures = 0;
    while (!job || !isTerminal(job.status)) {
      signal?.throwIfAborted();
      try {
        job = await backend.getJob(jobId);
        failures = 0;
        handlers.onJob(job);
        await fillArtifacts(job);
        if (isTerminal(job.status)) break;
      } catch (e) {
        if (isAppError(e, "NOT_FOUND") || isAppError(e, "UNAUTHORIZED")) throw e;
        failures++;
      }
      await clock.sleep(Math.min(30_000, (input.pollIntervalMs ?? 2_000) * 2 ** Math.min(failures, 4)), signal);
    }

    if (job.status === "succeeded") await fillArtifacts(job);
    await Promise.allSettled(pending);
    return job;
  }
}

export class FetchArtifact {
  constructor(private readonly d: { projects: ProjectRepositoryPort; cache: LayerCachePort }) {}

  /** cache/<jobId>/ に取得して展開（済みなら何もしない）。ローカルのパスを返す */
  execute(input: {
    project: Project;
    backend: ComputeBackendPort;
    jobId: string;
    artifact: Artifact;
    onProgress?: DownloadProgress;
  }): Promise<string> {
    const dir = this.d.projects.cacheDir(input.project, input.jobId);
    return this.d.cache.fetch(input.backend, dir, input.jobId, input.artifact, input.onProgress);
  }
}

export class CancelJob {
  execute(input: { backend: ComputeBackendPort; jobId: string }): Promise<void> {
    return input.backend.cancelJob(input.jobId);
  }
}

export class RetryJob {
  constructor(private readonly d: { submit: SubmitJob }) {}

  /** 同じパラメータで再投入する。依存先のジョブ ID はプロジェクトの最新のものに差し替える */
  async execute(input: {
    project: Project;
    backend: ComputeBackendPort;
    job: Job;
  }): Promise<{ job: Job; apply: ProjectMutation }> {
    const { project, job } = input;
    let params = job.params;
    const extra: SubmitExtra = {};
    if (job.type === "render" && "analyzeJobId" in params && project.jobs.analyze) {
      params = { ...params, analyzeJobId: project.jobs.analyze.jobId };
      extra.castHash = project.jobs.render?.castHash;
    }
    if (job.type === "export" && "renderJobId" in params && project.jobs.render) {
      params = { ...params, renderJobId: project.jobs.render.jobId };
      const prev = project.jobs.exports.find((e) => e.jobId === job.jobId);
      extra.overridesHash = prev?.overridesHash;
      extra.file = prev?.file;
      extra.settings = prev?.settings;
    }
    const res = await this.d.submit.execute({ project, backend: input.backend, type: job.type, params, extra });
    if (job.type !== "export") return res;
    // 失敗した書き出しの記録は新しいジョブで置き換える
    return {
      job: res.job,
      apply: (p) =>
        res.apply({ ...p, jobs: { ...p.jobs, exports: p.jobs.exports.filter((e) => e.jobId !== job.jobId) } }),
    };
  }
}

export class GetQueue {
  execute(backend: ComputeBackendPort): Promise<QueueItem[]> {
    return backend.queue();
  }
}

export class GetLogs {
  execute(input: { backend: ComputeBackendPort; jobId: string; tail?: number }): Promise<string> {
    return input.backend.logs(input.jobId, input.tail ?? 200);
  }
}

export type RestoreResult =
  | { status: "none" } // サーバーにまだ何も送っていない
  | { status: "offline"; error: AppError }
  | { status: "server_missing" } // サーバーのデータが消えた（404）
  | { status: "ok"; jobs: Job[]; active: Job[]; apply: ProjectMutation };

export class RestoreJobs {
  /**
   * 開いたプロジェクトをサーバーと照合する（F01 の ReconcileWithServer を兼ねる）。
   * 記録したジョブの状態を反映し、未完了のもの（再購読が要る）を返す。
   */
  async execute(input: { project: Project; backend: ComputeBackendPort }): Promise<RestoreResult> {
    const { project, backend } = input;
    const serverProjectId = project.compute.serverProjectId;
    if (!serverProjectId) return { status: "none" };
    let serverJobs: Job[];
    try {
      serverJobs = (await backend.getProject(serverProjectId)).jobs;
    } catch (e) {
      if (isAppError(e, "NOT_FOUND")) return { status: "server_missing" };
      return { status: "offline", error: toAppError(e) };
    }
    const jobs: Job[] = [];
    for (const id of allJobIds(project)) {
      const j = serverJobs.find((x) => x.jobId === id) ?? (await backend.getJob(id).catch(() => null));
      if (j) jobs.push(j);
    }
    const apply: ProjectMutation = (p) => jobs.reduce((acc, j) => syncJobStatus(acc, j), p);
    return { status: "ok", jobs, active: jobs.filter((j) => !isTerminal(j.status)), apply };
  }
}
