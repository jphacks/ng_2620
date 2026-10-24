// エディタ（F09 / F10）のユースケース：開くときのデータの組み立てと、手修正の保存

import { frameFile } from "@/domain/artifacts";
import { AppError } from "@/domain/errors";
import type { OccluderUnit } from "@/domain/occlusion";
import { parseUnits } from "@/domain/occlusion";
import type { OverridesDoc } from "@/domain/overrides";
import type { Project } from "@/domain/project";
import type { LocalFilePort } from "../ports/media";
import type { ProjectRepositoryPort } from "../ports/project";

/** エディタが 1 フレームを描くのに読むファイル（すべて絶対パス） */
export interface EditorPaths {
  frame(f: number): string;
  clean(f: number): string;
  avatar(personId: number, f: number): string;
  hide(personId: number, f: number): string;
  /** 遮蔽物ユニットのラベル（analyze の units が無ければ null） */
  units: ((f: number) => string) | null;
  /** 追跡マスク（8bit、値 = personId。無ければ null） */
  mask: ((f: number) => string) | null;
}

export interface EditorData {
  frameCount: number;
  fps: number;
  width: number;
  height: number;
  /** 置換した人物（layers.json の persons） */
  persons: number[];
  /** フレームごとの奥 → 手前の並び（layers.json の order） */
  order: Record<string, number[]>;
  units: OccluderUnit[];
  paths: EditorPaths;
}

export class LoadEditorData {
  constructor(private readonly d: { projects: ProjectRepositoryPort; files: LocalFilePort }) {}

  /** 描画済みのプロジェクトのレイヤー・ユニットの場所と索引を読む（取得済みであること。無ければ LAYERS_MISSING） */
  async execute(project: Project): Promise<EditorData> {
    const { projects, files } = this.d;
    const analyze = project.jobs.analyze?.jobId;
    const render = project.jobs.render?.jobId;
    const meta = project.meta;
    if (!analyze || !render || !meta) throw new AppError("LAYERS_MISSING", "描画済みのレイヤーがありません");
    const an = projects.cacheDir(project, analyze);
    const rd = projects.cacheDir(project, render);
    const layersJson = files.join(rd, "layers.json");
    if (!(await files.exists(layersJson))) throw new AppError("LAYERS_MISSING", "レイヤーをまだ取得していません");
    const layers = JSON.parse(await files.readText(layersJson)) as { persons?: number[]; order?: Record<string, number[]> };
    const unitsJson = files.join(an, "units.json");
    const units = (await files.exists(unitsJson)) ? parseUnits(JSON.parse(await files.readText(unitsJson))) : [];
    const hasUnits = await files.exists(files.join(an, "units", frameFile(0, "png")));
    const hasMasks = await files.exists(files.join(an, "masks", frameFile(0, "png")));
    const persons = layers.persons ?? [];
    return {
      frameCount: meta.frameCount,
      fps: meta.fps,
      width: meta.proxy.width,
      height: meta.proxy.height,
      persons,
      order: layers.order ?? {},
      units,
      paths: {
        frame: (f) => files.join(an, "frames", frameFile(f, "jpg")),
        clean: (f) => files.join(rd, "clean", frameFile(f, "webp")),
        avatar: (p, f) => files.join(rd, "avatar", String(p), frameFile(f, "webp")),
        hide: (p, f) => files.join(rd, "hide", String(p), frameFile(f, "png")),
        units: hasUnits ? (f) => files.join(an, "units", frameFile(f, "png")) : null,
        mask: hasMasks ? (f) => files.join(an, "masks", frameFile(f, "png")) : null,
      },
    };
  }
}

export class SaveOverrides {
  constructor(private readonly d: { projects: ProjectRepositoryPort }) {}
  /** overrides.json を保存（原子的に書く） */
  execute(projectId: string, doc: OverridesDoc): Promise<void> {
    return this.d.projects.saveOverrides(projectId, doc);
  }
}
