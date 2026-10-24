import { describe, expect, it } from "vitest";
import { AppError } from "./errors";
import { newJob } from "./job";
import { emptyOverrides } from "./overrides";
import {
  exportFileName,
  migrate,
  nameFromSourcePath,
  needsExport,
  needsRender,
  newProject,
  overridesHash,
  projectCastHash,
  projectPhase,
  recordJob,
  renderState,
  syncJobStatus,
  uniqueName,
} from "./project";

const source = {
  path: "/Users/me/Movies/IMG_0001.MOV",
  copied: false,
  durationSec: 9.8,
  width: 1080,
  height: 1920,
  fps: 29.97,
  hasAudio: true,
};
const p0 = () =>
  newProject({ id: "loc_1", name: "IMG_0001", now: "2026-10-24T00:00:00Z", source, profileId: "remote-win" });

describe("migrate", () => {
  it("現行版はそのまま読める（往復で同じ）", () => {
    const p = p0();
    expect(migrate(JSON.parse(JSON.stringify(p)))).toEqual(p);
  });

  it("v0（仮の旧版）→ v1", () => {
    const v0 = {
      id: "loc_old",
      name: "旧プロジェクト",
      created: "2026-10-01T00:00:00Z",
      updated: "2026-10-02T00:00:00Z",
      source,
      server: { profileId: "remote-win", projectId: "prj_9", mediaId: "med_9" },
      analyzeJobId: "job_a",
      renderJobId: "job_r",
    };
    const p = migrate(v0);
    expect(p.schemaVersion).toBe(1);
    expect(p.createdAt).toBe("2026-10-01T00:00:00Z");
    expect(p.updatedAt).toBe("2026-10-02T00:00:00Z");
    expect(p.compute).toEqual({
      profileId: "remote-win",
      serverProjectId: "prj_9",
      mediaId: "med_9",
      upload: undefined,
    });
    expect(p.jobs.analyze).toEqual({ jobId: "job_a", status: "succeeded", finishedAt: undefined });
    expect(p.jobs.render?.jobId).toBe("job_r");
    expect(p.finish).toEqual({ smooth: 0.05, footLock: true, spring: true });
    // 旧版の castHash は空 → 再描画が必要とは判定しない
    expect(needsRender(p)).toBe(false);
  });

  it("新しすぎる版は開かない", () => {
    expect(() => migrate({ ...p0(), schemaVersion: 2 })).toThrowError(AppError);
    try {
      migrate({ ...p0(), schemaVersion: 2 });
    } catch (e) {
      expect((e as AppError).code).toBe("PROJECT_TOO_NEW");
    }
  });

  it("必須項目が無ければ PROJECT_INVALID", () => {
    expect(() => migrate({ schemaVersion: 1, name: "x" })).toThrowError(/必須/);
    expect(() => migrate("x")).toThrowError(AppError);
  });
});

describe("再描画・再書き出しの要否", () => {
  const withRender = () => {
    let p = { ...p0(), cast: [{ personId: 1, action: "replace" as const, avatarId: "av_a" }] };
    const job = {
      ...newJob({
        jobId: "job_r",
        projectId: "prj",
        type: "render",
        params: { analyzeJobId: "job_a", cast: p.cast },
        createdAt: "t",
      }),
      status: "succeeded" as const,
    };
    p = recordJob(p, job, { castHash: projectCastHash(p) });
    return p;
  };

  it("描画後にキャストを変えると stale", () => {
    const p = withRender();
    expect(renderState(p)).toBe("fresh");
    const changed = { ...p, cast: [{ personId: 1, action: "replace" as const, avatarId: "av_b" }] };
    expect(needsRender(changed)).toBe(true);
    const finishChanged = { ...p, finish: { ...p.finish, smooth: 0.1 } };
    expect(needsRender(finishChanged)).toBe(true);
  });

  it("書き出し後に手修正を変えると再書き出しが必要", () => {
    let p = withRender();
    const doc = emptyOverrides();
    const job = {
      ...newJob({
        jobId: "job_e",
        projectId: "prj",
        type: "export",
        params: { renderJobId: "job_r", overrides: [], settings: { format: "mp4_h264" } },
        createdAt: "t",
      }),
      status: "succeeded" as const,
    };
    p = recordJob(p, job, { overridesHash: overridesHash(doc) });
    expect(needsExport(p, overridesHash(doc))).toBe(false);
    const edited = {
      ...doc,
      items: [
        {
          id: "ovr_1",
          kind: "unit" as const,
          personId: 1,
          unitId: 12,
          range: [40, 95] as [number, number],
          mode: "front" as const,
          createdAt: "t",
        },
      ],
    };
    expect(needsExport(p, overridesHash(edited))).toBe(true);
    // id と作成日時だけが違う修正は同じ内容とみなす
    const sameContent = { ...edited, items: [{ ...edited.items[0]!, id: "ovr_9", createdAt: "u" }] };
    expect(overridesHash(sameContent)).toBe(overridesHash(edited));
    expect(exportFileName(p)).toBe("IMG_0001_002.mp4");
  });
});

describe("ジョブの記録", () => {
  it("syncJobStatus は該当するジョブだけ更新し、変化が無ければ同じオブジェクト", () => {
    const job = newJob({ jobId: "job_a", projectId: "prj", type: "analyze", params: { mediaId: "m" }, createdAt: "t" });
    const p = recordJob(p0(), job);
    expect(projectPhase(p)).toBe("analyzing");
    const same = syncJobStatus(p, { jobId: "job_a", status: "queued" });
    expect(same).toBe(p);
    const done = syncJobStatus(p, { jobId: "job_a", status: "succeeded", finishedAt: "t2" });
    expect(done.jobs.analyze).toEqual({ jobId: "job_a", status: "succeeded", finishedAt: "t2" });
    expect(projectPhase(done)).toBe("analyzed");
    expect(syncJobStatus(p, { jobId: "job_x", status: "failed" })).toBe(p);
  });
});

describe("名前", () => {
  it("動画ファイル名から作り、重複時は連番", () => {
    expect(nameFromSourcePath("C:\\Users\\me\\Videos\\公園 vlog.mov")).toBe("公園 vlog");
    expect(nameFromSourcePath("/a/b/IMG_0001.MOV")).toBe("IMG_0001");
    expect(uniqueName("IMG_0001", [])).toBe("IMG_0001");
    expect(uniqueName("IMG_0001", ["img_0001", "IMG_0001 2"])).toBe("IMG_0001 3");
  });
});
