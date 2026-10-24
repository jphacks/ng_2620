// @vitest-environment node
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { LocalFilePort } from "../ports/media";
import type { ProjectRepositoryPort } from "../ports/project";
import type { Project } from "@/domain/project";
import { LoadEditorData } from "./editor";

const ROOT = join(__dirname, "../../../../../fixtures/synth/synth.vvlog");
const files = {
  exists: async (p: string) => existsSync(p),
  readText: async (p: string) => readFileSync(p, "utf8"),
  join: (b: string, ...parts: string[]) => join(b, ...parts),
} as unknown as LocalFilePort;
const projects = { cacheDir: (_p: Project, jobId: string) => join(ROOT, "cache", jobId) } as unknown as ProjectRepositoryPort;
const project = JSON.parse(readFileSync(join(ROOT, "project.json"), "utf8")) as Project;

describe("LoadEditorData（フィクスチャ v0）", () => {
  it("レイヤーの索引・ユニット・各フレームのパスを組み立てる", async () => {
    const d = await new LoadEditorData({ projects, files }).execute(project);
    expect([d.frameCount, d.fps, d.width, d.height]).toEqual([96, 30, 540, 960]);
    expect(d.persons).toEqual([1, 2]);
    expect(d.order["48"]).toEqual([1, 2]);
    expect(d.units.map((u) => u.unitId)).toEqual([1, 2]);
    expect(existsSync(d.paths.avatar(2, 95))).toBe(true);
    expect(existsSync(d.paths.units!(0))).toBe(true);
    expect(d.paths.frame(7).endsWith(join("job_synth_analyze", "frames", "00007.jpg"))).toBe(true);
  });

  it("描画前のプロジェクトは LAYERS_MISSING", async () => {
    const p = { ...project, jobs: { exports: [] } } as Project;
    await expect(new LoadEditorData({ projects, files }).execute(p)).rejects.toMatchObject({ code: "LAYERS_MISSING" });
  });
});

describe("フィクスチャ v0 の project.json", () => {
  it("castHash がアプリの計算と一致する（キャスト欄に「再描画が必要」が出ない）", async () => {
    const { castHash } = await import("@/domain/cast");
    expect(project.jobs.render?.castHash).toBe(castHash(project.cast, project.finish));
  });
});
