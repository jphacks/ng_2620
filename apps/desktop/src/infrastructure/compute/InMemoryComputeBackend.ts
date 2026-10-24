// 開発用のシミュレーター（VITE_BACKEND=memory、接続先 memory://sim）。GPU もモックサーバーも無しでウィザードを端から端まで流す。
// GPU 1 枚 = 1 ワーカーの FIFO キュー、依存ジョブ待ち、SSE 相当のイベント（snapshot → … → job_finished）、Last-Event-ID の再開、
// 取り消し、失敗の再現（fail: "track:NO_PERSON_FOUND"）。成果物は canvas で合成した画像（simArtifacts.ts）

import type { ChunkBody, ComputeBackendPort, CreateUploadReq, QueueItem } from "@/application/ports/compute";
import { AppError } from "@/domain/errors";
import type { Artifact, ArtifactKind, Job, JobEvent, JobParams, JobType, StageName } from "@/domain/job";
import { applyEvent, isTerminal, newJob, STAGES } from "@/domain/job";
import { DEFAULT_CHUNK_SIZE, chunkCount } from "@/domain/upload";
import type { FileSystem } from "../fs/FileSystem";
import { writeStoreZip } from "../fs/storeZip";
import { sha256Hex } from "../media/hashers";
import type { SimSynth } from "./simArtifacts";
import { SIM_META, SIM_PERSONS } from "./simArtifacts";

const STAGE_SEC: Record<StageName, number> = {
  ingest: 2,
  track: 6,
  mesh: 6,
  occlusion: 4,
  removal: 3,
  avatar: 6,
  layers: 3,
  composite: 5,
  encode: 2,
};
const TICK_MS = 250;

interface StoredArtifact {
  artifact: Artifact;
  bytes: Uint8Array;
}

interface Upload {
  size: number;
  chunks: Set<number>;
  sourcePath?: string;
  projectId: string;
}

export interface SimOptions {
  fs: FileSystem;
  synth: SimSynth | null;
  speed?: number;
  /** "stage:CODE" の形。最初にそのステージに達したジョブを失敗させる */
  fail?: string;
}

export class InMemoryComputeBackend implements ComputeBackendPort {
  readonly profileId = "dev-memory";
  private seq = 0;
  private projects = new Map<string, { name: string; mediaIds: string[]; sources: Map<string, string | undefined> }>();
  private uploads = new Map<string, Upload>();
  private jobs = new Map<string, Job>();
  private log = new Map<string, JobEvent[]>();
  private listeners = new Map<string, Set<() => void>>();
  private artifacts = new Map<string, Map<string, StoredArtifact>>();
  private logLines = new Map<string, string[]>();
  private queueOrder: string[] = [];
  private running: string | null = null;
  private canceled = new Set<string>();
  private avatars = new Set<string>();
  private failPending: { stage: StageName; code: string } | null;
  private readonly speed: number;

  constructor(private readonly o: SimOptions) {
    this.speed = o.speed && o.speed > 0 ? o.speed : 1;
    const [stage, code] = (o.fail ?? "").split(":");
    this.failPending = stage && code ? { stage: stage as StageName, code } : null;
  }

  private id(prefix: string) {
    return `${prefix}_sim${(++this.seq).toString(36)}`;
  }

  // --- 共通 ---

  async health() {
    return { status: "ok" as const, version: "sim", apiVersion: 1 };
  }

  async capabilities() {
    return { gpuName: "シミュレーター", vramMb: null, limits: {}, queueLength: this.queueOrder.length + (this.running ? 1 : 0) };
  }

  async createProject(name: string) {
    const projectId = this.id("prj");
    this.projects.set(projectId, { name, mediaIds: [], sources: new Map() });
    return { projectId };
  }

  async getProject(projectId: string) {
    const p = this.projects.get(projectId);
    if (!p) throw new AppError("NOT_FOUND", "プロジェクトがありません", { status: 404 });
    return { projectId, mediaIds: p.mediaIds, jobs: [...this.jobs.values()].filter((j) => j.projectId === projectId) };
  }

  async deleteProject(projectId: string) {
    this.projects.delete(projectId);
  }

  // --- アップロード ---

  async createUpload(req: CreateUploadReq) {
    if (!this.projects.has(req.projectId)) throw new AppError("NOT_FOUND", "プロジェクトがありません", { status: 404 });
    const uploadId = this.id("upl");
    this.uploads.set(uploadId, { size: req.size, chunks: new Set(), projectId: req.projectId });
    return { uploadId, chunkSize: DEFAULT_CHUNK_SIZE };
  }

  async uploadChunk(uploadId: string, index: number, body: ChunkBody) {
    const u = this.uploads.get(uploadId);
    if (!u) throw new AppError("NOT_FOUND", "アップロードがありません", { status: 404 });
    if (!(body instanceof Uint8Array)) u.sourcePath = body.path;
    await new Promise((r) => setTimeout(r, 60 / this.speed));
    u.chunks.add(index);
  }

  async getUpload(uploadId: string) {
    const u = this.uploads.get(uploadId);
    if (!u) throw new AppError("NOT_FOUND", "アップロードがありません", { status: 404 });
    return { receivedChunks: [...u.chunks].sort((a, b) => a - b), size: u.size };
  }

  async completeUpload(uploadId: string) {
    const u = this.uploads.get(uploadId);
    if (!u) throw new AppError("NOT_FOUND", "アップロードがありません", { status: 404 });
    if (u.chunks.size < chunkCount(u.size, DEFAULT_CHUNK_SIZE)) throw new AppError("UPLOAD_INCOMPLETE", "断片が足りません", { status: 409 });
    const mediaId = this.id("med");
    const p = this.projects.get(u.projectId);
    p?.mediaIds.push(mediaId);
    p?.sources.set(mediaId, u.sourcePath);
    return { mediaId, meta: SIM_META };
  }

  // --- アバター ---

  async registerAvatar(file: { name: string; bytes: Uint8Array }) {
    const avatarId = `av_${(await sha256Hex(file.bytes)).slice(0, 16)}`;
    this.avatars.add(avatarId);
    return { avatarId };
  }

  async hasAvatar(avatarId: string) {
    return this.avatars.has(avatarId);
  }

  // --- ジョブ ---

  async submitJob(projectId: string, type: JobType, params: JobParams) {
    if (!this.projects.has(projectId)) throw new AppError("NOT_FOUND", "プロジェクトがありません", { status: 404 });
    const dependsOn = "analyzeJobId" in params ? params.analyzeJobId : "renderJobId" in params ? params.renderJobId : undefined;
    const job = newJob({ jobId: this.id("job"), projectId, type, params, createdAt: new Date().toISOString(), dependsOn });
    this.jobs.set(job.jobId, job);
    this.log.set(job.jobId, []);
    this.logLines.set(job.jobId, [`[sim] ${type} を受け付けました`]);
    this.queueOrder.push(job.jobId);
    this.updatePositions();
    queueMicrotask(() => void this.pump());
    return this.jobs.get(job.jobId)!;
  }

  async getJob(jobId: string) {
    const j = this.jobs.get(jobId);
    if (!j) throw new AppError("NOT_FOUND", "ジョブがありません", { status: 404 });
    return j;
  }

  async listJobs(projectId: string) {
    return [...this.jobs.values()].filter((j) => j.projectId === projectId);
  }

  async queue(): Promise<QueueItem[]> {
    const items: QueueItem[] = [];
    if (this.running) {
      const j = this.jobs.get(this.running)!;
      items.push({ jobId: j.jobId, projectId: j.projectId, type: j.type, status: j.status, position: 0 });
    }
    this.queueOrder.forEach((id, i) => {
      const j = this.jobs.get(id)!;
      items.push({ jobId: id, projectId: j.projectId, type: j.type, status: j.status, position: i + (this.running ? 1 : 0) });
    });
    return items;
  }

  async cancelJob(jobId: string) {
    const j = await this.getJob(jobId);
    if (isTerminal(j.status)) return;
    if (this.running === jobId) {
      this.canceled.add(jobId);
      return;
    }
    this.queueOrder = this.queueOrder.filter((id) => id !== jobId);
    this.emit(jobId, { type: "job_finished", data: { status: "canceled" } });
    this.updatePositions();
  }

  async *events(jobId: string, opts: { lastEventId?: string; signal?: AbortSignal } = {}): AsyncGenerator<JobEvent> {
    const job = await this.getJob(jobId);
    const events = this.log.get(jobId)!;
    let idx = events.length;
    yield { id: events.at(-1)?.id, type: "snapshot", data: job };
    if (isTerminal(job.status)) return;
    for (;;) {
      while (idx < events.length) {
        const ev = events[idx++]!;
        yield ev;
        if (ev.type === "job_finished" || ev.type === "job_failed") return;
      }
      await new Promise<void>((resolve, reject) => {
        const set = this.listeners.get(jobId) ?? new Set();
        this.listeners.set(jobId, set);
        const wake = () => {
          set.delete(wake);
          resolve();
        };
        set.add(wake);
        opts.signal?.addEventListener("abort", () => {
          set.delete(wake);
          reject(opts.signal!.reason);
        });
      });
    }
  }

  async listArtifacts(jobId: string) {
    return [...(this.artifacts.get(jobId)?.values() ?? [])].map((a) => a.artifact);
  }

  async downloadArtifact(jobId: string, name: string, dest: string, onProgress?: (r: number, t: number | null) => void) {
    const a = this.artifacts.get(jobId)?.get(name);
    if (!a) throw new AppError("NOT_FOUND", "成果物がありません", { status: 404 });
    onProgress?.(0, a.bytes.length);
    await this.o.fs.writeBytes(dest, a.bytes);
    onProgress?.(a.bytes.length, a.bytes.length);
  }

  async logs(jobId: string, tail = 200) {
    return (this.logLines.get(jobId) ?? []).slice(-tail).join("\n");
  }

  // --- 実行 ---

  private emit(jobId: string, ev: JobEvent) {
    const events = this.log.get(jobId)!;
    const withId = { ...ev, id: String(events.length + 1) } as JobEvent;
    events.push(withId);
    this.jobs.set(jobId, applyEvent(this.jobs.get(jobId)!, withId, new Date().toISOString()));
    for (const l of [...(this.listeners.get(jobId) ?? [])]) l();
  }

  private updatePositions() {
    this.queueOrder.forEach((id, i) => {
      const j = this.jobs.get(id)!;
      const position = i + (this.running ? 1 : 0);
      if (j.queuePosition !== position) this.emit(id, { type: "queued", data: { position } });
    });
  }

  private async pump(): Promise<void> {
    if (this.running) return;
    for (const id of this.queueOrder) {
      const job = this.jobs.get(id)!;
      const dep = job.dependsOn ? this.jobs.get(job.dependsOn) : undefined;
      if (dep && (dep.status === "failed" || dep.status === "canceled")) {
        this.queueOrder = this.queueOrder.filter((x) => x !== id);
        this.emit(id, { type: "job_failed", data: { stage: STAGES[job.type][0]!, error: { code: "DEPENDENCY_NOT_READY", message: "依存するジョブが失敗しました" }, logTail: "" } });
        return this.pump();
      }
      if (dep && dep.status !== "succeeded") continue;
      this.queueOrder = this.queueOrder.filter((x) => x !== id);
      this.running = id;
      this.updatePositions();
      try {
        await this.run(job);
      } finally {
        this.running = null;
        this.updatePositions();
        void this.pump();
      }
      return;
    }
  }

  private async run(job: Job) {
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms / this.speed));
    const say = (line: string) => this.logLines.get(job.jobId)!.push(`[${new Date().toISOString()}] ${line}`);
    for (const stage of STAGES[job.type]) {
      this.emit(job.jobId, { type: "stage_started", data: { stage } });
      say(`${stage} 開始`);
      const ticks = Math.max(1, Math.round((STAGE_SEC[stage] * 1000) / TICK_MS));
      for (let t = 1; t <= ticks; t++) {
        await sleep(TICK_MS);
        if (this.canceled.has(job.jobId)) {
          say("取り消されました");
          this.emit(job.jobId, { type: "job_finished", data: { status: "canceled" } });
          return;
        }
        if (this.failPending?.stage === stage && t >= ticks / 2) {
          const { code } = this.failPending;
          this.failPending = null;
          say(`${stage} で失敗：${code}`);
          this.emit(job.jobId, { type: "job_failed", data: { stage, error: { code, message: `${stage} で失敗しました（シミュレーター）` }, logTail: (await this.logs(job.jobId, 20)) } });
          return;
        }
        const frames = SIM_META.frameCount;
        this.emit(job.jobId, {
          type: "progress",
          data: { stage, progress: t / ticks, etaSec: ((ticks - t) * TICK_MS) / 1000 / this.speed, message: `frame ${Math.round((frames * t) / ticks)}/${frames}` },
        });
      }
      for (const a of await this.produce(job, stage)) this.emit(job.jobId, { type: "artifact_ready", data: a });
      this.emit(job.jobId, { type: "stage_finished", data: { stage, durationSec: STAGE_SEC[stage] / this.speed } });
      say(`${stage} 完了`);
    }
    this.emit(job.jobId, { type: "job_finished", data: { status: "succeeded" } });
  }

  private async put(job: Job, stage: StageName, name: string, kind: ArtifactKind, contentType: string, bytes: Uint8Array): Promise<Artifact> {
    const artifact: Artifact = {
      name,
      kind,
      stage,
      size: bytes.length,
      sha256: await sha256Hex(bytes),
      contentType,
      url: `/v1/jobs/${job.jobId}/artifacts/${name}`,
    };
    const m = this.artifacts.get(job.jobId) ?? new Map<string, StoredArtifact>();
    m.set(name, { artifact, bytes });
    this.artifacts.set(job.jobId, m);
    return artifact;
  }

  private async produce(job: Job, stage: StageName): Promise<Artifact[]> {
    const enc = new TextEncoder();
    const json = (name: string, v: unknown) => this.put(job, stage, name, "json", "application/json", enc.encode(JSON.stringify(v)));
    const zip = async (name: string, entries: Promise<{ name: string; bytes: Uint8Array }[]> | null) =>
      this.put(job, stage, name, "zip", "application/zip", writeStoreZip(entries ? await entries : [{ name: `${name}/SIMULATED.txt`, bytes: enc.encode("sim") }]));
    const { synth } = this.o;
    switch (stage) {
      case "ingest":
        return [await json("meta", SIM_META), await zip("proxy_frames", synth?.frames(SIM_META) ?? null)];
      case "track":
        return [
          await json(
            "persons",
            SIM_PERSONS.map(({ color: _c, ...p }) => ({ ...p, bbox: [40, 120, 90, 220], thumb: `person_thumbs/${p.personId}.jpg` })),
          ),
          await zip("person_thumbs", synth?.thumbs() ?? null),
          await zip("track_masks", synth?.masks(SIM_META) ?? null),
        ];
      case "occlusion":
        return [
          await json("units_index", {
            units: [
              { unitId: 1, frames: [0, 59], label: "object", tracks: [1], areaPx: 4000, perPerson: { "1": { auto: "behind", why: "S-", confidence: 0.4, overlapFrames: [5, 55] } } },
            ],
          }),
        ];
      case "layers": {
        const cast = "cast" in job.params ? job.params.cast : [];
        return [
          await json("layers_index", { persons: cast.map((c) => c.personId), order: {}, avatarIds: Object.fromEntries(cast.map((c) => [String(c.personId), c.avatarId])) }),
          await zip("layers", null),
        ];
      }
      case "encode": {
        const render = this.jobs.get("renderJobId" in job.params ? job.params.renderJobId : "");
        const analyze = render && "analyzeJobId" in render.params ? this.jobs.get(render.params.analyzeJobId) : undefined;
        const mediaId = analyze && "mediaId" in analyze.params ? analyze.params.mediaId : undefined;
        const source = mediaId ? this.projects.get(job.projectId)?.sources.get(mediaId) : undefined;
        // 書き出しの代わりに元の動画をそのまま返す（モックの mp4）
        const bytes = source ? await this.o.fs.readBytes(source) : new Uint8Array();
        return [await this.put(job, stage, "export_video", "video", "video/mp4", bytes)];
      }
      default:
        return [];
    }
  }
}
