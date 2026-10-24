import { describe, expect, it } from "vitest";
import type { Job, JobEvent } from "./job";
import { applyEvent, currentStage, newJob, overallProgress, orderedStages } from "./job";

const base = (): Job =>
  newJob({
    jobId: "job_1",
    projectId: "prj_1",
    type: "analyze",
    params: { mediaId: "med_1" },
    createdAt: "2026-10-24T00:00:00Z",
  });

const replay = (events: JobEvent[], start = base()) =>
  events.reduce((j, e) => applyEvent(j, e, "2026-10-24T00:01:00Z"), start);

const artifact = (name: string) => ({
  name,
  kind: "json" as const,
  stage: "track" as const,
  size: 1,
  sha256: "x",
  contentType: "application/json",
  url: `/v1/jobs/job_1/artifacts/${name}`,
});

describe("applyEvent", () => {
  it("イベント列を再生して成功まで進む", () => {
    const j = replay([
      { type: "queued", data: { position: 1 } },
      { type: "stage_started", data: { stage: "ingest" } },
      { type: "progress", data: { stage: "ingest", progress: 0.5, etaSec: 3 } },
      { type: "stage_finished", data: { stage: "ingest", durationSec: 4 } },
      { type: "stage_started", data: { stage: "track" } },
      { type: "artifact_ready", data: artifact("persons") },
      { type: "progress", data: { stage: "track", progress: 0.62, etaSec: 40, message: "frame 120/300" } },
    ]);
    expect(j.status).toBe("running");
    expect(j.queuePosition).toBeUndefined();
    expect(j.stages[0]).toMatchObject({ name: "ingest", status: "done", progress: 1, durationSec: 4 });
    expect(currentStage(j)).toMatchObject({ name: "track", progress: 0.62, etaSec: 40, message: "frame 120/300" });
    expect(j.artifacts).toEqual(["persons"]);

    const done = replay([{ type: "job_finished", data: { status: "succeeded" } }], j);
    expect(done.status).toBe("succeeded");
    expect(done.stages.every((s) => s.status === "done")).toBe(true);
    expect(overallProgress(done)).toBe(1);
  });

  it("snapshot は全置換", () => {
    const snap: Job = { ...base(), status: "running", artifacts: ["meta"] };
    const j = replay([
      { type: "progress", data: { stage: "ingest", progress: 0.3 } },
      { type: "snapshot", data: snap },
    ]);
    expect(j).toBe(snap);
  });

  it("再送・順不同のイベントで巻き戻らない", () => {
    const j = replay([
      { type: "progress", data: { stage: "track", progress: 0.8 } },
      { type: "progress", data: { stage: "track", progress: 0.4 } }, // 古い進捗の再送
      { type: "stage_finished", data: { stage: "track", durationSec: 30 } },
      { type: "progress", data: { stage: "track", progress: 0.9 } }, // 終了後に届いた古いイベント
      { type: "stage_started", data: { stage: "track" } },
      { type: "artifact_ready", data: artifact("persons") },
      { type: "artifact_ready", data: artifact("persons") },
    ]);
    const track = j.stages.find((s) => s.name === "track");
    expect(track).toMatchObject({ status: "done", progress: 1 });
    expect(j.artifacts).toEqual(["persons"]);
  });

  it("終了したジョブは後から来た進捗で running に戻らない", () => {
    const j = replay([
      {
        type: "job_failed",
        data: { stage: "track", error: { code: "NO_PERSON_FOUND", message: "x" }, logTail: "..." },
      },
      { type: "progress", data: { stage: "track", progress: 0.5 } },
    ]);
    expect(j.status).toBe("failed");
    expect(j.error?.code).toBe("NO_PERSON_FOUND");
    expect(j.error?.detail).toMatchObject({ stage: "track", logTail: "..." });
    expect(j.stages.find((s) => s.name === "track")?.status).toBe("failed");
  });

  it("取り消し", () => {
    const j = replay([{ type: "job_finished", data: { status: "canceled" } }]);
    expect(j.status).toBe("canceled");
    expect(j.stages.every((s) => s.status === "pending")).toBe(true);
  });
});

describe("overallProgress", () => {
  it("ステージの重みで合計する（ingest 0.05 + track 0.35 × 0.5）", () => {
    const j = replay([
      { type: "stage_finished", data: { stage: "ingest", durationSec: 1 } },
      { type: "progress", data: { stage: "track", progress: 0.5 } },
    ]);
    expect(overallProgress(j)).toBeCloseTo(0.05 + 0.35 * 0.5);
  });

  it("サーバーが送っていないステージは pending で補う", () => {
    const j: Job = { ...base(), stages: [] };
    expect(orderedStages(j).map((s) => s.name)).toEqual(["ingest", "track", "mesh", "occlusion"]);
    expect(overallProgress(j)).toBe(0);
  });
});
