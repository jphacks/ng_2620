// ユースケースのテスト用のポート実装（インメモリ・呼び出し記録つき）

import type { Avatar } from "@/domain/avatar";
import { AppError } from "@/domain/errors";
import type { Artifact, Job, JobEvent, JobParams, JobType } from "@/domain/job";
import { newJob } from "@/domain/job";
import type { MediaMeta } from "@/domain/media";
import type { OverridesDoc } from "@/domain/overrides";
import { emptyOverrides } from "@/domain/overrides";
import type { Project } from "@/domain/project";
import type { AvatarLibraryPort } from "../ports/avatar";
import type { Capabilities, ChunkBody, ComputeBackendPort, CreateUploadReq, Health, QueueItem, ServerProject } from "../ports/compute";
import type { FileHasherPort, FileStat, LocalFilePort } from "../ports/media";
import type { ProjectRepositoryPort, RecentEntry } from "../ports/project";
import type { ClockPort, IdPort } from "../ports/system";

export const META: MediaMeta = {
  durationSec: 9.8,
  frameCount: 294,
  fps: 30,
  full: { width: 1080, height: 1920 },
  proxy: { width: 540, height: 960 },
  proxyScale: 2,
  hasAudio: true,
  sourceCodec: "hevc",
};

export class FakeClock implements ClockPort {
  t = Date.parse("2026-10-24T00:00:00Z");
  sleeps: number[] = [];
  now() {
    return new Date(this.t);
  }
  async sleep(ms: number, signal?: AbortSignal) {
    signal?.throwIfAborted();
    this.sleeps.push(ms);
    this.t += ms;
    await Promise.resolve();
  }
}

export class SeqIds implements IdPort {
  n = 0;
  newId(prefix: string) {
    return `${prefix}_${++this.n}`;
  }
}

/** events() は scripts の先頭から 1 回の接続ぶんずつ流す。Error を入れるとその時点で切断 */
export type EventScript = (JobEvent | Error)[];

export class FakeBackend implements ComputeBackendPort {
  readonly profileId = "fake";
  calls: string[] = [];
  jobs = new Map<string, Job>();
  artifacts = new Map<string, Artifact[]>();
  scripts: EventScript[] = [];
  received = new Map<string, Set<number>>();
  uploads = new Map<string, { size: number; chunkSize: number }>();
  avatarsOnServer = new Set<string>();
  failComplete: AppError[] = [];
  serverProjects = new Map<string, ServerProject>();
  private seq = 0;
  chunkSize = 4;

  async health(): Promise<Health> {
    this.calls.push("health");
    return { status: "ok", version: "test", apiVersion: 1 };
  }
  async capabilities(): Promise<Capabilities> {
    this.calls.push("capabilities");
    return { gpuName: "RTX", vramMb: 16000, limits: {}, queueLength: 0 };
  }
  async createProject(name: string) {
    this.calls.push(`createProject:${name}`);
    const projectId = `prj_${++this.seq}`;
    this.serverProjects.set(projectId, { projectId, mediaIds: [], jobs: [] });
    return { projectId };
  }
  async getProject(projectId: string) {
    const p = this.serverProjects.get(projectId);
    if (!p) throw new AppError("NOT_FOUND", "no project", { status: 404 });
    return { ...p, jobs: [...this.jobs.values()].filter((j) => j.projectId === projectId) };
  }
  async deleteProject(projectId: string) {
    this.calls.push(`deleteProject:${projectId}`);
    this.serverProjects.delete(projectId);
  }
  async createUpload(req: CreateUploadReq) {
    this.calls.push(`createUpload:${req.filename}:${req.size}`);
    const uploadId = `upl_${++this.seq}`;
    this.uploads.set(uploadId, { size: req.size, chunkSize: this.chunkSize });
    this.received.set(uploadId, new Set());
    return { uploadId, chunkSize: this.chunkSize };
  }
  async uploadChunk(uploadId: string, index: number, _body: ChunkBody) {
    this.calls.push(`chunk:${index}`);
    const r = this.received.get(uploadId);
    if (!r) throw new AppError("NOT_FOUND", "no upload");
    r.add(index);
  }
  async getUpload(uploadId: string) {
    const r = this.received.get(uploadId);
    const u = this.uploads.get(uploadId);
    if (!r || !u) throw new AppError("NOT_FOUND", "no upload", { status: 404 });
    return { receivedChunks: [...r].sort((a, b) => a - b), size: u.size };
  }
  async completeUpload(uploadId: string) {
    this.calls.push("complete");
    const err = this.failComplete.shift();
    if (err) throw err;
    if (!this.uploads.has(uploadId)) throw new AppError("NOT_FOUND", "no upload");
    return { mediaId: "med_1", meta: META };
  }
  async registerAvatar(file: { name: string; bytes: Uint8Array }) {
    this.calls.push(`registerAvatar:${file.name}`);
    const avatarId = new TextDecoder().decode(file.bytes);
    this.avatarsOnServer.add(avatarId);
    return { avatarId };
  }
  async hasAvatar(avatarId: string) {
    return this.avatarsOnServer.has(avatarId);
  }
  async submitJob(projectId: string, type: JobType, params: JobParams) {
    this.calls.push(`submit:${type}`);
    const job = newJob({ jobId: `job_${type}_${++this.seq}`, projectId, type, params, createdAt: "2026-10-24T00:00:00Z" });
    this.jobs.set(job.jobId, job);
    return job;
  }
  async getJob(jobId: string) {
    this.calls.push(`getJob:${jobId}`);
    const j = this.jobs.get(jobId);
    if (!j) throw new AppError("NOT_FOUND", "no job", { status: 404 });
    return j;
  }
  async listJobs(projectId: string) {
    return [...this.jobs.values()].filter((j) => j.projectId === projectId);
  }
  async queue(): Promise<QueueItem[]> {
    return [];
  }
  async cancelJob(jobId: string) {
    this.calls.push(`cancel:${jobId}`);
  }
  async *events(jobId: string): AsyncIterable<JobEvent> {
    this.calls.push(`events:${jobId}`);
    const script = this.scripts.shift();
    if (!script) throw new AppError("NETWORK", "no script");
    for (const ev of script) {
      if (ev instanceof Error) throw ev;
      yield ev;
    }
  }
  async listArtifacts(jobId: string) {
    this.calls.push(`listArtifacts:${jobId}`);
    return this.artifacts.get(jobId) ?? [];
  }
  async downloadArtifact(jobId: string, name: string, dest: string) {
    this.calls.push(`download:${jobId}:${name}:${dest}`);
  }
  async logs() {
    return "log";
  }
}

export class FakeFiles implements LocalFilePort {
  texts = new Map<string, string>();
  sizes = new Map<string, number>();
  async exists(path: string) {
    return this.texts.has(path) || this.sizes.has(path);
  }
  async stat(path: string): Promise<FileStat> {
    return { size: this.sizes.get(path) ?? 0, mtimeMs: 0 };
  }
  async readText(path: string) {
    const t = this.texts.get(path);
    if (t === undefined) throw new Error(`no file ${path}`);
    return t;
  }
  async readBytes(path: string) {
    return new TextEncoder().encode(await this.readText(path));
  }
  async readChunk() {
    return new Uint8Array();
  }
  async writeBytes(path: string, bytes: Uint8Array) {
    this.texts.set(path, new TextDecoder().decode(bytes));
  }
  toUrl(path: string) {
    return `file://${path}`;
  }
  join(base: string, ...parts: string[]) {
    return [base, ...parts].join("/");
  }
}

export class FakeHasher implements FileHasherPort {
  count = 0;
  async sha256(_path: string, onProgress?: (r: number) => void) {
    this.count++;
    onProgress?.(1);
    return "a".repeat(64);
  }
}

export class FakeRepo implements ProjectRepositoryPort {
  projects = new Map<string, { path: string; project: Project }>();
  overrides = new Map<string, OverridesDoc>();
  recent: RecentEntry[] = [];
  removed: string[] = [];
  async defaultDir() {
    return "/Movies/vvlog";
  }
  async allocate(name: string, dir?: string) {
    return { name, path: `${dir ?? "/Movies/vvlog"}/${name}.vvlog` };
  }
  async create(path: string, project: Project) {
    this.projects.set(project.id, { path, project });
  }
  async open(path: string, migrate: (raw: unknown) => Project) {
    const hit = [...this.projects.values()].find((x) => x.path === path);
    if (!hit) throw new Error("not found");
    return migrate(JSON.parse(JSON.stringify(hit.project)));
  }
  async save(project: Project) {
    const cur = this.projects.get(project.id);
    this.projects.set(project.id, { path: cur?.path ?? `/x/${project.id}`, project });
  }
  async saveOverrides(projectId: string, doc: OverridesDoc) {
    this.overrides.set(projectId, doc);
  }
  async loadOverrides(projectId: string) {
    return this.overrides.get(projectId) ?? emptyOverrides();
  }
  pathOf(projectId: string) {
    return this.projects.get(projectId)?.path ?? null;
  }
  resolve(projectId: string, rel: string) {
    return `${this.pathOf(projectId)}/${rel}`;
  }
  cacheDir(project: Project, jobId: string) {
    return `${this.pathOf(project.id)}/cache/${jobId}`;
  }
  exportsDir(project: Project) {
    return `${this.pathOf(project.id)}/exports`;
  }
  async listRecent() {
    return this.recent;
  }
  async touchRecent(entry: RecentEntry) {
    this.recent = [entry, ...this.recent.filter((e) => e.path !== entry.path)];
  }
  async forgetRecent(path: string) {
    this.recent = this.recent.filter((e) => e.path !== path);
  }
  async remove(path: string) {
    this.removed.push(path);
  }
  async clearCache() {}
}

export class FakeAvatars implements AvatarLibraryPort {
  items: Avatar[] = [];
  async list() {
    return this.items;
  }
  async get(id: string) {
    return this.items.find((a) => a.avatarId === id) ?? null;
  }
  async import(path: string): Promise<Avatar> {
    throw new AppError("AVATAR_INVALID", path);
  }
  async remove(id: string) {
    this.items = this.items.filter((a) => a.avatarId !== id);
  }
  async readBytes(id: string) {
    return new TextEncoder().encode(id);
  }
}

export function avatar(id: string, name = id): Avatar {
  return { avatarId: id, sha256: id.padEnd(64, "0"), file: `/lib/${id}.vrm`, meta: { name, authors: [], version: "1.0", license: {} }, importedAt: "2026-10-24T00:00:00Z" };
}
