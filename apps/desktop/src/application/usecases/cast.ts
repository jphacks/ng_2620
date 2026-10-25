// F06 キャスト：人物一覧の読み込み、割当、描画ジョブの投入

import { cacheRel } from "@/domain/artifacts";
import type { Avatar } from "@/domain/avatar";
import type { CastDraftRow } from "@/domain/cast";
import { canRender, draftFromCast, MAX_TARGETS, parsePersons, renamePerson, toCast } from "@/domain/cast";
import { AppError } from "@/domain/errors";
import type { Job } from "@/domain/job";
import type { MediaMeta } from "@/domain/media";
import type { Project, ProjectMutation } from "@/domain/project";
import { projectCastHash } from "@/domain/project";
import type { AvatarLibraryPort } from "../ports/avatar";
import type { ComputeBackendPort } from "../ports/compute";
import type { LocalFilePort } from "../ports/media";
import type { ProjectRepositoryPort } from "../ports/project";
import type { SubmitJob } from "./jobs";

export class LoadAnalysisResult {
  constructor(private readonly d: { projects: ProjectRepositoryPort; files: LocalFilePort }) {}

  /** analyze の meta / persons（取得済み）をプロジェクトに取り込む。ユーザーが付けた人物名は残す */
  async execute(input: {
    project: Project;
    jobId: string;
    name: "meta" | "persons";
    defaultLabel: (personId: number) => string;
  }): Promise<ProjectMutation> {
    const { projects, files } = this.d;
    const dir = projects.cacheDir(input.project, input.jobId);
    if (input.name === "meta") {
      const meta = JSON.parse(await files.readText(files.join(dir, "meta.json"))) as MediaMeta;
      return (p) => ({ ...p, meta });
    }
    const raw: unknown = JSON.parse(await files.readText(files.join(dir, "persons.json")));
    return (p) => ({
      ...p,
      persons: parsePersons(raw, {
        previous: p.persons,
        defaultLabel: input.defaultLabel,
        thumbRel: (t) => cacheRel(input.jobId, t),
      }),
    });
  }
}

export class UpdateCast {
  /** 下書きのうち、選択済みでアバターのある行を project.cast に。使うアバターの情報も記録する */
  execute(input: { draft: readonly CastDraftRow[]; avatars: readonly Avatar[] }): ProjectMutation {
    const cast = toCast(input.draft);
    return (p) => {
      const refs = { ...p.avatars };
      for (const c of cast) {
        const a = input.avatars.find((x) => x.avatarId === c.avatarId);
        if (a) refs[a.avatarId] = { name: a.meta.name, file: a.file, sha256: a.sha256 };
      }
      return { ...p, cast, avatars: refs };
    };
  }
}

export class RenamePerson {
  execute(input: { personId: number; label: string; fallback: string }): ProjectMutation {
    return (p) => ({ ...p, persons: renamePerson(p.persons, input.personId, input.label, input.fallback) });
  }
}

export class StartRender {
  constructor(private readonly d: { avatars: AvatarLibraryPort; submit: SubmitJob }) {}

  /** 未登録の VRM をサーバーに登録してから render を投入（analyze の完了はサーバーが待つ） */
  async execute(input: {
    project: Project;
    backend: ComputeBackendPort;
    draft?: readonly CastDraftRow[];
    maxTargets?: number;
  }): Promise<{ job: Job; apply: ProjectMutation }> {
    const { avatars, submit } = this.d;
    const { backend } = input;
    const check = canRender(
      input.draft ?? draftFromCast(input.project.persons, input.project.cast),
      input.maxTargets ?? MAX_TARGETS,
    );
    if (!check.ok) throw new AppError("VALIDATION_FAILED", "キャストが確定していません", { detail: { ...check } });
    const analyzeJobId = input.project.jobs.analyze?.jobId;
    if (!analyzeJobId) throw new AppError("DEPENDENCY_NOT_READY", "解析がまだ始まっていません");

    for (const avatarId of new Set(check.cast.map((c) => c.avatarId))) {
      if (await backend.hasAvatar(avatarId)) continue;
      const avatar = await avatars.get(avatarId);
      if (!avatar)
        throw new AppError("AVATAR_INVALID", "アバターがライブラリにありません。取り込み直してください", {
          detail: { avatarId },
        });
      const bytes = await avatars.readBytes(avatarId);
      await backend.registerAvatar({ name: `${avatar.meta.name || avatarId}.vrm`, bytes });
    }

    const project = { ...input.project, cast: check.cast };
    const res = await submit.execute({
      project,
      backend,
      type: "render",
      params: { analyzeJobId, cast: check.cast, finish: project.finish },
      extra: { castHash: projectCastHash(project) },
    });
    return { job: res.job, apply: (p) => res.apply({ ...p, cast: check.cast }) };
  }
}
