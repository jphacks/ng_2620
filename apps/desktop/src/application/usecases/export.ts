// F11 書き出し（アプリ側）：export ジョブの投入と、完成した MP4 の保存

import { AppError } from "@/domain/errors";
import type { ExportSettings, Job } from "@/domain/job";
import type { Project, ProjectMutation } from "@/domain/project";
import { composeMutations, exportFileName, needsRender, overridesHash, setExportFile, syncJobStatus } from "@/domain/project";
import type { ComputeBackendPort, DownloadProgress } from "../ports/compute";
import type { LocalFilePort } from "../ports/media";
import type { ProjectRepositoryPort } from "../ports/project";
import type { SubmitJob } from "./jobs";

/** 画質の選択肢（S4） */
export const QUALITY_PRESETS = { standard: 18, high: 14, light: 23 } as const;
export type QualityPreset = keyof typeof QUALITY_PRESETS;

export class StartExport {
  constructor(private readonly d: { projects: ProjectRepositoryPort; submit: SubmitJob }) {}

  /**
   * 手修正を同梱して export を投入する。render 後にキャストが変わっていれば止める（DEPENDENCY_NOT_READY / needs_render）。
   * render が実行中でも投入できる（サーバーが完了を待つ）。
   */
  async execute(input: { project: Project; backend: ComputeBackendPort; settings: ExportSettings; file?: string }): Promise<{ job: Job; apply: ProjectMutation; overridesCount: number }> {
    const { projects, submit } = this.d;
    const { project } = input;
    if (needsRender(project)) throw new AppError("DEPENDENCY_NOT_READY", "再描画が必要です", { detail: { reason: "needs_render" } });
    const render = project.jobs.render;
    if (!render || render.status === "failed" || render.status === "canceled") {
      throw new AppError("DEPENDENCY_NOT_READY", "アバターの描画が完了していません", { detail: { reason: "no_render" } });
    }
    const doc = await projects.loadOverrides(project.id);
    // ブラシ（P1）は PNG を base64 で載せる必要があるので、P0 ではユニットの修正だけを送る
    const overrides = doc.items.filter((o) => o.kind === "unit");
    const file = input.file ?? `exports/${exportFileName(project)}`;
    const res = await submit.execute({
      project,
      backend: input.backend,
      type: "export",
      params: { renderJobId: render.jobId, overrides, settings: input.settings },
      extra: { overridesHash: overridesHash(doc), file, settings: input.settings },
    });
    return { ...res, overridesCount: overrides.length };
  }
}

export class SaveExportResult {
  constructor(private readonly d: { projects: ProjectRepositoryPort; files: LocalFilePort }) {}

  /** export_video をダウンロードして保存先に置き、project.jobs.exports に記録する。保存先の絶対パスを返す */
  async execute(input: { project: Project; backend: ComputeBackendPort; job: Job; onProgress?: DownloadProgress }): Promise<{ path: string; apply: ProjectMutation }> {
    const { projects } = this.d;
    const { project, job } = input;
    const ref = project.jobs.exports.find((e) => e.jobId === job.jobId);
    const fileRel = ref?.file ?? `exports/${exportFileName(project)}`;
    const path = isAbsolute(fileRel) ? fileRel : projects.resolve(project.id, fileRel);
    await input.backend.downloadArtifact(job.jobId, "export_video", path, input.onProgress);
    return { path, apply: composeMutations((p) => syncJobStatus(p, job), (p) => setExportFile(p, job.jobId, fileRel)) };
  }
}

export function isAbsolute(path: string): boolean {
  return path.startsWith("/") || /^[a-zA-Z]:[\\/]/.test(path) || path.startsWith("\\\\");
}
