// F01 プロジェクトの作成・保存・再開・削除

import { displaySize } from "@/domain/media";
import type { LocalMediaInfo } from "@/domain/media";
import type { OverridesDoc } from "@/domain/overrides";
import type { Project } from "@/domain/project";
import { migrate, nameFromSourcePath, newProject, projectPhase, touch } from "@/domain/project";
import { isAppError } from "@/domain/errors";
import type { ComputeBackendPort } from "../ports/compute";
import type { ProjectRepositoryPort, RecentEntry } from "../ports/project";
import type { ClockPort, IdPort } from "../ports/system";

export function recentEntryOf(projects: ProjectRepositoryPort, project: Project, path: string, overridesCount: number): RecentEntry {
  const thumbRel = project.persons.find((p) => p.thumb)?.thumb;
  return {
    path,
    projectId: project.id,
    name: project.name,
    updatedAt: project.updatedAt,
    phase: projectPhase(project),
    overridesCount,
    thumb: thumbRel ? projects.resolve(project.id, thumbRel) : undefined,
    isSample: !!project.sample,
  };
}

export class CreateProject {
  constructor(private readonly d: { projects: ProjectRepositoryPort; clock: ClockPort; ids: IdPort }) {}

  /** 動画から名前を付けてプロジェクトを作る（既定の保存先 ~/Movies/vvlog/<名前>.vvlog/） */
  async execute(input: { sourcePath: string; info: LocalMediaInfo; profileId: string | null; dir?: string }): Promise<{ project: Project; path: string }> {
    const { projects, clock, ids } = this.d;
    const { name, path } = await projects.allocate(nameFromSourcePath(input.sourcePath), input.dir);
    const size = displaySize(input.info);
    const project = newProject({
      id: ids.newId("loc"),
      name,
      now: clock.now().toISOString(),
      profileId: input.profileId,
      source: {
        path: input.sourcePath,
        copied: false,
        durationSec: input.info.durationSec,
        width: size.width,
        height: size.height,
        fps: input.info.fps ?? 0,
        hasAudio: input.info.hasAudio ?? false,
      },
    });
    await projects.create(path, project);
    await projects.touchRecent(recentEntryOf(projects, project, path, 0));
    return { project, path };
  }
}

export class OpenProject {
  constructor(private readonly d: { projects: ProjectRepositoryPort }) {}

  /** マイグレーションして開く。新しすぎる版は PROJECT_TOO_NEW。サーバーとの照合は RestoreJobs */
  async execute(path: string): Promise<{ project: Project; overrides: OverridesDoc; path: string }> {
    const { projects } = this.d;
    const project = await projects.open(path, migrate);
    const overrides = await projects.loadOverrides(project.id);
    await projects.touchRecent(recentEntryOf(projects, project, path, overrides.items.length));
    return { project, overrides, path };
  }
}

export class SaveProject {
  constructor(private readonly d: { projects: ProjectRepositoryPort; clock: ClockPort }) {}

  /** 原子的に保存し、最近の一覧を更新する。保存した（updatedAt を進めた）プロジェクトを返す */
  async execute(project: Project, opts: { overridesCount?: number } = {}): Promise<Project> {
    const { projects, clock } = this.d;
    const saved = touch(project, clock.now().toISOString());
    await projects.save(saved);
    const path = projects.pathOf(saved.id);
    if (path) {
      const count = opts.overridesCount ?? (await projects.loadOverrides(saved.id)).items.length;
      await projects.touchRecent(recentEntryOf(projects, saved, path, count));
    }
    return saved;
  }
}

export class LoadOverrides {
  constructor(private readonly d: { projects: ProjectRepositoryPort }) {}
  /** 手修正（overrides.json）を読む。書き出し画面の件数表示と再書き出しの要否に使う */
  execute(projectId: string): Promise<OverridesDoc> {
    return this.d.projects.loadOverrides(projectId);
  }
}

export class ListRecentProjects {
  constructor(private readonly d: { projects: ProjectRepositoryPort }) {}
  execute(): Promise<RecentEntry[]> {
    return this.d.projects.listRecent();
  }
}

export class DeleteProject {
  constructor(private readonly d: { projects: ProjectRepositoryPort }) {}

  /** ローカルはゴミ箱へ。alsoServer ならサーバー側のデータも消す（無ければ無視） */
  async execute(input: { path: string; alsoServer: boolean; backend?: ComputeBackendPort }): Promise<void> {
    const { projects } = this.d;
    if (input.alsoServer && input.backend) {
      const project = await projects.open(input.path, migrate);
      const serverProjectId = project.compute.serverProjectId;
      if (serverProjectId) {
        try {
          await input.backend.deleteProject(serverProjectId);
        } catch (e) {
          if (!isAppError(e, "NOT_FOUND")) throw e;
        }
      }
    }
    await projects.remove(input.path);
    await projects.forgetRecent(input.path);
  }
}
