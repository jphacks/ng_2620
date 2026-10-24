import { beforeEach, describe, expect, it } from "vitest";
import { AppError } from "@/domain/errors";
import type { Artifact, Job, JobEvent } from "@/domain/job";
import { newJob } from "@/domain/job";
import type { Project } from "@/domain/project";
import { newProject, needsRender, recordJob } from "@/domain/project";
import { avatar, FakeAvatars, FakeBackend, FakeClock, FakeFiles, FakeHasher, FakeRepo, SeqIds } from "../testing/fakes";
import { TestConnection } from "./connection";
import { StartExport } from "./export";
import { RestoreJobs, RetryJob, SubmitJob, SubscribeJob } from "./jobs";
import { ImportMedia } from "./media";
import { CreateProject } from "./project";
import { LoadAnalysisResult, StartRender, UpdateCast } from "./cast";

const SRC = "/Users/me/Movies/IMG_0001.MOV";
const baseProject = (): Project =>
  newProject({
    id: "loc_1",
    name: "IMG_0001",
    now: "2026-10-24T00:00:00Z",
    profileId: "fake",
    source: { path: SRC, copied: false, durationSec: 9.8, width: 1080, height: 1920, fps: 30, hasAudio: true },
  });

const artifact = (name: string, jobId = "job_a"): Artifact => ({
  name,
  kind: "json",
  stage: "track",
  size: 1,
  sha256: "x",
  contentType: "application/json",
  url: `/v1/jobs/${jobId}/artifacts/${name}`,
});

describe("CreateProject", () => {
  it("動画ファイル名から名前を付けて作り、最近の一覧に載せる", async () => {
    const repo = new FakeRepo();
    const uc = new CreateProject({ projects: repo, clock: new FakeClock(), ids: new SeqIds() });
    const { project, path } = await uc.execute({
      sourcePath: SRC,
      info: { durationSec: 9.8, width: 1920, height: 1080, rotation: 90, codec: "hevc", fps: 29.97, hasAudio: true, sizeBytes: 1 },
      profileId: "remote-win",
    });
    expect(path).toBe("/Movies/vvlog/IMG_0001.vvlog");
    expect(project.source).toMatchObject({ width: 1080, height: 1920, fps: 29.97 }); // 回転を適用
    expect(project.compute.profileId).toBe("remote-win");
    expect(repo.recent[0]).toMatchObject({ name: "IMG_0001", phase: "draft" });
  });
});

describe("ImportMedia", () => {
  let backend: FakeBackend;
  let files: FakeFiles;
  let hasher: FakeHasher;
  let uc: ImportMedia;
  beforeEach(() => {
    backend = new FakeBackend();
    files = new FakeFiles();
    files.sizes.set(SRC, 10); // chunkSize 4 → 3 断片
    hasher = new FakeHasher();
    uc = new ImportMedia({ files, hasher, clock: new FakeClock() });
  });

  it("プロジェクト作成 → sha256 → 全断片 → complete。途中の ID を記録する", async () => {
    let p = baseProject();
    const res = await uc.execute({ project: p, backend, onCheckpoint: (f) => (p = f(p)) });
    expect(backend.calls.filter((c) => c.startsWith("chunk:")).sort()).toEqual(["chunk:0", "chunk:1", "chunk:2"]);
    expect(p.compute.serverProjectId).toBe("prj_1");
    expect(p.compute.upload?.uploadId).toBeDefined();
    expect(p.source.sha256).toHaveLength(64);
    p = res.apply(p);
    expect(p.compute).toMatchObject({ mediaId: "med_1", upload: undefined });
    expect(p.meta?.proxy).toEqual({ width: 540, height: 960 });
  });

  it("再開：受信済みの断片は送らず、sha256 も再計算しない", async () => {
    let p = baseProject();
    // 1 回目は complete の前に中断した想定
    const first = await backend.createProject("IMG_0001");
    const { uploadId } = await backend.createUpload({ projectId: first.projectId, kind: "source", filename: "IMG_0001.MOV", size: 10, sha256: "a" });
    await backend.uploadChunk(uploadId, 0, new Uint8Array());
    await backend.uploadChunk(uploadId, 2, new Uint8Array());
    p = { ...p, source: { ...p.source, sha256: "b".repeat(64) }, compute: { ...p.compute, serverProjectId: first.projectId, upload: { uploadId, chunkSize: 4 } } };
    backend.calls = [];

    await uc.execute({ project: p, backend });
    expect(backend.calls).toEqual(["chunk:1", "complete"]);
    expect(hasher.count).toBe(0);
  });

  it("UPLOAD_INCOMPLETE なら欠けた断片だけ送り直して complete をやり直す", async () => {
    backend.failComplete.push(new AppError("UPLOAD_INCOMPLETE", "missing"));
    const p = baseProject();
    const origUpload = backend.uploadChunk.bind(backend);
    let dropped = false;
    backend.uploadChunk = async (id, index, body) => {
      if (index === 1 && !dropped) {
        dropped = true; // 1 回目の断片 1 は届かなかった
        return;
      }
      return origUpload(id, index, body);
    };
    await uc.execute({ project: p, backend });
    expect(backend.calls.filter((c) => c === "complete")).toHaveLength(2);
    expect(backend.calls.filter((c) => c === "chunk:1")).toHaveLength(1);
  });

  it("サーバー側でアップロードが消えていたら最初から", async () => {
    const p = { ...baseProject(), compute: { profileId: "fake", serverProjectId: "prj_x", upload: { uploadId: "upl_gone", chunkSize: 4 } } };
    await uc.execute({ project: p, backend });
    expect(backend.calls.some((c) => c.startsWith("createUpload"))).toBe(true);
  });
});

describe("SubscribeJob", () => {
  const setup = () => {
    const backend = new FakeBackend();
    const job = newJob({ jobId: "job_a", projectId: "prj_1", type: "analyze", params: { mediaId: "med_1" }, createdAt: "t" });
    backend.jobs.set(job.jobId, job);
    return { backend, job, uc: new SubscribeJob({ clock: new FakeClock() }) };
  };

  it("イベントを流し、成果物を重複なく渡し、終了で返る", async () => {
    const { backend, job, uc } = setup();
    backend.artifacts.set("job_a", [artifact("meta"), artifact("persons")]);
    const events: JobEvent[] = [
      { id: "1", type: "snapshot", data: { ...job, status: "running", artifacts: ["meta"] } },
      { id: "2", type: "artifact_ready", data: artifact("meta") }, // snapshot で補った後の再送
      { id: "3", type: "artifact_ready", data: artifact("persons") },
      { id: "4", type: "job_finished", data: { status: "succeeded" } },
    ];
    backend.scripts.push(events);
    const got: string[] = [];
    const states: string[] = [];
    const final = await uc.execute({
      backend,
      jobId: "job_a",
      handlers: { onJob: (j) => states.push(j.status), onArtifact: (a) => void got.push(a.name) },
    });
    expect(final.status).toBe("succeeded");
    expect(got).toEqual(["meta", "persons"]);
    expect(states.at(-1)).toBe("succeeded");
  });

  it("SSE が使えなければポーリングに落として終了まで追う", async () => {
    const { backend, job, uc } = setup();
    backend.scripts.push([new AppError("NETWORK", "stream unsupported")]);
    let polls = 0;
    backend.getJob = async () => {
      polls++;
      return polls < 3 ? { ...job, status: "running" } : { ...job, status: "failed", error: { code: "GPU_OOM", message: "oom" } };
    };
    let fellBack = false;
    const final = await uc.execute({ backend, jobId: "job_a", handlers: { onJob: () => {}, onFallback: () => (fellBack = true) } });
    expect(fellBack).toBe(true);
    expect(final.status).toBe("failed");
    expect(polls).toBe(3);
  });
});

describe("StartRender", () => {
  it("未登録の VRM だけ登録して render を投入し、castHash を記録する", async () => {
    const backend = new FakeBackend();
    backend.avatarsOnServer.add("av_b");
    const avatars = new FakeAvatars();
    avatars.items = [avatar("av_a"), avatar("av_b")];
    const analyze = { ...newJob({ jobId: "job_a", projectId: "prj_1", type: "analyze", params: { mediaId: "m" }, createdAt: "t" }), status: "running" as const };
    let p = recordJob({ ...baseProject(), compute: { profileId: "fake", serverProjectId: "prj_1", mediaId: "m" } }, analyze);
    const uc = new StartRender({ avatars, submit: new SubmitJob() });
    const { job, apply } = await uc.execute({
      project: p,
      backend,
      draft: [
        { personId: 1, selected: true, avatarId: "av_a" },
        { personId: 2, selected: true, avatarId: "av_b" },
      ],
    });
    expect(backend.calls.filter((c) => c.startsWith("registerAvatar"))).toEqual(["registerAvatar:av_a.vrm"]);
    expect(job.params).toMatchObject({ analyzeJobId: "job_a", cast: [{ personId: 1 }, { personId: 2 }] });
    p = apply(p);
    expect(p.jobs.render?.jobId).toBe(job.jobId);
    expect(needsRender(p)).toBe(false);
    // キャストを変えると再描画が必要
    p = new UpdateCast().execute({ draft: [{ personId: 1, selected: true, avatarId: "av_b" }], avatars: avatars.items })(p);
    expect(needsRender(p)).toBe(true);
  });

  it("アバター未割当の人物がいれば投入しない", async () => {
    const uc = new StartRender({ avatars: new FakeAvatars(), submit: new SubmitJob() });
    await expect(
      uc.execute({ project: baseProject(), backend: new FakeBackend(), draft: [{ personId: 1, selected: true, avatarId: null }] }),
    ).rejects.toMatchObject({ code: "VALIDATION_FAILED" });
  });
});

describe("StartExport", () => {
  it("再描画が必要なら止める", async () => {
    const repo = new FakeRepo();
    const render = { ...newJob({ jobId: "job_r", projectId: "prj_1", type: "render", params: { analyzeJobId: "a", cast: [] }, createdAt: "t" }), status: "succeeded" as const };
    let p = recordJob({ ...baseProject(), compute: { profileId: "fake", serverProjectId: "prj_1" } }, render, { castHash: "old" });
    const uc = new StartExport({ projects: repo, submit: new SubmitJob() });
    await expect(uc.execute({ project: p, backend: new FakeBackend(), settings: { format: "mp4_h264" } })).rejects.toMatchObject({
      code: "DEPENDENCY_NOT_READY",
      detail: { reason: "needs_render" },
    });
    // 現在のキャストで描画済みなら手修正を同梱して投入
    p = recordJob(p, render);
    await repo.create("/p/IMG_0001.vvlog", p);
    repo.overrides.set(p.id, {
      schemaVersion: 1,
      items: [{ id: "ovr_1", kind: "unit", personId: 1, unitId: 12, range: [40, 95], mode: "front", createdAt: "t" }],
    });
    const backend = new FakeBackend();
    const { job, apply, overridesCount } = await uc.execute({ project: p, backend, settings: { format: "mp4_h264", crf: 18 } });
    expect(overridesCount).toBe(1);
    expect(job.params).toMatchObject({ renderJobId: "job_r", overrides: [{ unitId: 12 }], settings: { crf: 18 } });
    expect(apply(p).jobs.exports[0]).toMatchObject({ jobId: job.jobId, file: "exports/IMG_0001_001.mp4" });
  });
});

describe("RestoreJobs", () => {
  it("サーバーに無ければ server_missing", async () => {
    const p = { ...baseProject(), compute: { profileId: "fake", serverProjectId: "prj_gone" } };
    expect(await new RestoreJobs().execute({ project: p, backend: new FakeBackend() })).toEqual({ status: "server_missing" });
  });

  it("記録したジョブの状態を反映し、未完了のものを返す", async () => {
    const backend = new FakeBackend();
    const { projectId } = await backend.createProject("x");
    const a = await backend.submitJob(projectId, "analyze", { mediaId: "m" });
    const r = await backend.submitJob(projectId, "render", { analyzeJobId: a.jobId, cast: [] });
    let p = recordJob(recordJob({ ...baseProject(), compute: { profileId: "fake", serverProjectId: projectId } }, a), r);
    backend.jobs.set(a.jobId, { ...a, status: "succeeded" });
    backend.jobs.set(r.jobId, { ...r, status: "running" });
    const res = await new RestoreJobs().execute({ project: p, backend });
    if (res.status !== "ok") throw new Error(res.status);
    p = res.apply(p);
    expect(p.jobs.analyze?.status).toBe("succeeded");
    expect(res.active.map((j: Job) => j.jobId)).toEqual([r.jobId]);
  });
});

describe("RetryJob", () => {
  it("render の再試行は最新の analyze を使う", async () => {
    const backend = new FakeBackend();
    const failed = { ...newJob({ jobId: "job_r1", projectId: "prj_1", type: "render", params: { analyzeJobId: "job_old", cast: [] }, createdAt: "t" }), status: "failed" as const };
    const analyze = { ...newJob({ jobId: "job_new", projectId: "prj_1", type: "analyze", params: { mediaId: "m" }, createdAt: "t" }), status: "succeeded" as const };
    const p = recordJob(recordJob({ ...baseProject(), compute: { profileId: "fake", serverProjectId: "prj_1" } }, analyze), failed, { castHash: "h" });
    const { job, apply } = await new RetryJob({ submit: new SubmitJob() }).execute({ project: p, backend, job: failed });
    expect(job.params).toMatchObject({ analyzeJobId: "job_new" });
    expect(apply(p).jobs.render).toMatchObject({ jobId: job.jobId, castHash: "h" });
  });
});

describe("TestConnection", () => {
  const profile = { id: "p", name: "p", kind: "remote" as const, baseUrl: "https://x", tokenRef: "p" };
  const factory = (b: Partial<FakeBackend>) => ({ forProfile: () => Object.assign(new FakeBackend(), b), offline: () => new FakeBackend() });

  it("届かない / API 版違い / トークン違いを段階別に返す", async () => {
    const unreachable = await new TestConnection({ compute: factory({ health: () => Promise.reject(new AppError("NETWORK", "x")) }) }).execute(profile);
    expect(unreachable).toMatchObject({ ok: false, failedStep: "reach" });
    const version = await new TestConnection({ compute: factory({ health: async () => ({ status: "ok" as const, version: "x", apiVersion: 2 }) }) }).execute(profile);
    expect(version).toMatchObject({ ok: false, failedStep: "api_version" });
    const auth = await new TestConnection({ compute: factory({ capabilities: () => Promise.reject(new AppError("UNAUTHORIZED", "x", { status: 401 })) }) }).execute(profile);
    expect(auth).toMatchObject({ ok: false, failedStep: "auth" });
    const ok = await new TestConnection({ compute: factory({}) }).execute(profile);
    expect(ok).toMatchObject({ ok: true, capabilities: { gpuName: "RTX" } });
  });
});

describe("LoadAnalysisResult", () => {
  it("persons.json を取り込み、サムネイルをプロジェクト相対にする", async () => {
    const repo = new FakeRepo();
    const files = new FakeFiles();
    const p = baseProject();
    await repo.create("/p/x.vvlog", p);
    files.texts.set("/p/x.vvlog/cache/job_a/persons.json", JSON.stringify([{ personId: 1, firstFrame: 0, lastFrame: 10, visibleRatio: 0.9, thumb: "person_thumbs/1.jpg" }]));
    const apply = await new LoadAnalysisResult({ projects: repo, files }).execute({ project: p, jobId: "job_a", name: "persons", defaultLabel: (n) => `人物 ${n}` });
    expect(apply(p).persons).toEqual([
      { personId: 1, label: "人物 1", firstFrame: 0, lastFrame: 10, visibleRatio: 0.9, bestFrame: undefined, bbox: undefined, thumb: "cache/job_a/person_thumbs/1.jpg" },
    ]);
  });
});
