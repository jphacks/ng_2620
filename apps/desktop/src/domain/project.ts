// プロジェクト（contracts §6.1、ADR-0007）。project.json の正本の型、マイグレーション、状態の判定

import type { CastEntry, FinishParams, Person } from "./cast";
import { castHash, DEFAULT_FINISH } from "./cast";
import { AppError } from "./errors";
import { hashValue } from "./hash";
import type { ExportSettings, Job, JobStatus } from "./job";
import { isTerminal } from "./job";
import type { MediaMeta } from "./media";
import type { OverridesDoc } from "./overrides";
import { DEFAULT_CHUNK_SIZE } from "./upload";
import { fileNameOf } from "./validation";

export const PROJECT_SCHEMA_VERSION = 1;
export const PROJECT_EXT = ".vvlog";

export interface SourceMedia {
  path: string;
  copied: boolean;
  sha256?: string;
  durationSec: number;
  width: number; // 回転適用後
  height: number;
  fps: number;
  hasAudio: boolean;
}

export interface JobRef {
  jobId: string;
  status: JobStatus;
  finishedAt?: string;
}

export interface RenderJobRef extends JobRef {
  castHash: string;
}

export interface ExportJobRef extends JobRef {
  file?: string; // プロジェクト相対（exports/…）か絶対パス
  overridesHash: string;
  renderJobId?: string;
  settings?: ExportSettings;
}

export interface ProjectJobs {
  analyze?: JobRef;
  render?: RenderJobRef;
  exports: ExportJobRef[];
}

export interface ComputeBinding {
  profileId: string | null;
  serverProjectId?: string;
  mediaId?: string;
  /** 再開用：送信途中のアップロード（契約への追加候補。complete で消す） */
  upload?: { uploadId: string; chunkSize: number };
}

export interface AvatarRef {
  name: string;
  file: string;
  sha256: string;
}

export interface UiState {
  playhead: number;
  zoom: number;
  panelLayout: string;
}

export interface SampleInfo {
  id: string;
  license: string;
  exportedVideo?: string;
}

export interface Project {
  schemaVersion: typeof PROJECT_SCHEMA_VERSION;
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  source: SourceMedia;
  compute: ComputeBinding;
  jobs: ProjectJobs;
  meta: MediaMeta | null;
  persons: Person[];
  cast: CastEntry[];
  finish: FinishParams;
  avatars: Record<string, AvatarRef>;
  ui: UiState;
  sample: SampleInfo | null;
  owner: null; // エンタープライズ用の予約
  createdBy: null;
}

export function newProject(input: { id: string; name: string; now: string; source: SourceMedia; profileId: string | null }): Project {
  return {
    schemaVersion: PROJECT_SCHEMA_VERSION,
    id: input.id,
    name: input.name,
    createdAt: input.now,
    updatedAt: input.now,
    source: input.source,
    compute: { profileId: input.profileId },
    jobs: { exports: [] },
    meta: null,
    persons: [],
    cast: [],
    finish: { ...DEFAULT_FINISH },
    avatars: {},
    ui: { playhead: 0, zoom: 1, panelLayout: "default" },
    sample: null,
    owner: null,
    createdBy: null,
  };
}

export function touch(p: Project, now: string): Project {
  return { ...p, updatedAt: now };
}

// --- 読み込みとマイグレーション ---

type Raw = Record<string, unknown>;
const isObj = (v: unknown): v is Raw => !!v && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : d);
const num = (v: unknown, d = 0): number => (typeof v === "number" && Number.isFinite(v) ? v : d);
const bool = (v: unknown, d = false): boolean => (typeof v === "boolean" ? v : d);

const JOB_STATUSES: readonly JobStatus[] = ["queued", "running", "succeeded", "failed", "canceled"];
const status = (v: unknown): JobStatus => (JOB_STATUSES.includes(v as JobStatus) ? (v as JobStatus) : "queued");

function jobRef(v: unknown): JobRef | undefined {
  if (!isObj(v) || typeof v.jobId !== "string") return undefined;
  return { jobId: v.jobId, status: status(v.status), finishedAt: typeof v.finishedAt === "string" ? v.finishedAt : undefined };
}

/**
 * 仮の旧版（v0）→ v1。v0 は schemaVersion を持たず、created / updated、server { profileId, projectId, mediaId }、
 * analyzeJobId / renderJobId を平らに持っていた形とする（マイグレーションの経路を先に用意しておくため）。
 */
function migrateV0(raw: Raw): Raw {
  const server = isObj(raw.server) ? raw.server : {};
  const jobs: Raw = { exports: [] };
  if (typeof raw.analyzeJobId === "string") jobs.analyze = { jobId: raw.analyzeJobId, status: "succeeded" };
  if (typeof raw.renderJobId === "string") jobs.render = { jobId: raw.renderJobId, status: "succeeded", castHash: "" };
  return {
    ...raw,
    schemaVersion: 1,
    createdAt: raw.createdAt ?? raw.created,
    updatedAt: raw.updatedAt ?? raw.updated ?? raw.created,
    compute: { profileId: server.profileId ?? null, serverProjectId: server.projectId, mediaId: server.mediaId },
    jobs,
  };
}

function normalizeV1(raw: Raw): Project {
  const src = isObj(raw.source) ? raw.source : null;
  if (typeof raw.id !== "string" || !raw.id || typeof raw.name !== "string" || !src || typeof src.path !== "string") {
    throw new AppError("PROJECT_INVALID", "project.json に必須の項目がありません");
  }
  const compute = isObj(raw.compute) ? raw.compute : {};
  const jobs = isObj(raw.jobs) ? raw.jobs : {};
  const render = jobRef(jobs.render);
  const exportsRaw = Array.isArray(jobs.exports) ? jobs.exports : [];
  const ui = isObj(raw.ui) ? raw.ui : {};
  const now = str(raw.updatedAt) || str(raw.createdAt) || new Date(0).toISOString();
  return {
    schemaVersion: PROJECT_SCHEMA_VERSION,
    id: raw.id,
    name: raw.name,
    createdAt: str(raw.createdAt, now),
    updatedAt: str(raw.updatedAt, now),
    source: {
      path: src.path,
      copied: bool(src.copied),
      sha256: typeof src.sha256 === "string" ? src.sha256 : undefined,
      durationSec: num(src.durationSec),
      width: num(src.width),
      height: num(src.height),
      fps: num(src.fps),
      hasAudio: bool(src.hasAudio),
    },
    compute: {
      profileId: typeof compute.profileId === "string" ? compute.profileId : null,
      serverProjectId: typeof compute.serverProjectId === "string" ? compute.serverProjectId : undefined,
      mediaId: typeof compute.mediaId === "string" ? compute.mediaId : undefined,
      upload:
        isObj(compute.upload) && typeof compute.upload.uploadId === "string"
          ? { uploadId: compute.upload.uploadId, chunkSize: num(compute.upload.chunkSize, DEFAULT_CHUNK_SIZE) }
          : undefined,
    },
    jobs: {
      analyze: jobRef(jobs.analyze),
      render: render ? { ...render, castHash: str((jobs.render as Raw).castHash) } : undefined,
      exports: exportsRaw.flatMap((e): ExportJobRef[] => {
        const r = jobRef(e);
        if (!r) return [];
        const er = e as Raw;
        return [
          {
            ...r,
            file: typeof er.file === "string" ? er.file : undefined,
            overridesHash: str(er.overridesHash),
            renderJobId: typeof er.renderJobId === "string" ? er.renderJobId : undefined,
            settings: isObj(er.settings) ? (er.settings as unknown as ExportSettings) : undefined,
          },
        ];
      }),
    },
    meta: isObj(raw.meta) && typeof raw.meta.frameCount === "number" ? (raw.meta as unknown as MediaMeta) : null,
    persons: Array.isArray(raw.persons) ? (raw.persons as Person[]).filter((p) => isObj(p) && typeof p.personId === "number") : [],
    cast: Array.isArray(raw.cast)
      ? (raw.cast as CastEntry[]).filter((c) => isObj(c) && typeof c.personId === "number" && typeof c.avatarId === "string")
      : [],
    finish: { ...DEFAULT_FINISH, ...(isObj(raw.finish) ? (raw.finish as FinishParams) : {}) },
    avatars: isObj(raw.avatars) ? (raw.avatars as Record<string, AvatarRef>) : {},
    ui: { playhead: num(ui.playhead), zoom: num(ui.zoom, 1), panelLayout: str(ui.panelLayout, "default") },
    sample: isObj(raw.sample) && typeof raw.sample.id === "string" ? (raw.sample as unknown as SampleInfo) : null,
    owner: null,
    createdBy: null,
  };
}

/** project.json の中身を現行版の Project に。新しすぎる版は開かない */
export function migrate(raw: unknown): Project {
  if (!isObj(raw)) throw new AppError("PROJECT_INVALID", "project.json が JSON オブジェクトではありません");
  const v = raw.schemaVersion === undefined ? 0 : raw.schemaVersion;
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) {
    throw new AppError("PROJECT_INVALID", "schemaVersion が不正です");
  }
  if (v > PROJECT_SCHEMA_VERSION) {
    throw new AppError("PROJECT_TOO_NEW", "新しい版のアプリで作られています", { detail: { schemaVersion: v } });
  }
  let cur: Raw = raw;
  if (v === 0) cur = migrateV0(cur);
  return normalizeV1(cur);
}

// --- 描画・書き出しの要否 ---

export function projectCastHash(p: Project): string {
  return castHash(p.cast, p.finish);
}

/** 書き出しに効く内容（手修正の中身）のハッシュ。id・作成日時は含めない */
export function overridesHash(doc: OverridesDoc): string {
  return hashValue(doc.items.map(({ id: _id, createdAt: _c, ...rest }) => rest));
}

export type RenderState = "none" | "running" | "failed" | "fresh" | "stale";

export function renderState(p: Project): RenderState {
  const r = p.jobs.render;
  if (!r) return "none";
  if (r.castHash && r.castHash !== projectCastHash(p)) return "stale";
  if (r.status === "failed" || r.status === "canceled") return "failed";
  if (!isTerminal(r.status)) return "running";
  return "fresh";
}

/** 描画後にキャスト・仕上げを変えた */
export function needsRender(p: Project): boolean {
  return renderState(p) === "stale";
}

export function lastExport(p: Project): ExportJobRef | undefined {
  for (let i = p.jobs.exports.length - 1; i >= 0; i--) {
    const e = p.jobs.exports[i];
    if (e?.status === "succeeded") return e;
  }
  return undefined;
}

/** 前回の書き出し以降に手修正か描画が変わった */
export function needsExport(p: Project, currentOverridesHash: string): boolean {
  const last = lastExport(p);
  if (!last) return false;
  if (last.overridesHash !== currentOverridesHash) return true;
  return !!(last.renderJobId && p.jobs.render && last.renderJobId !== p.jobs.render.jobId);
}

// --- ジョブの記録 ---

export function recordJob(p: Project, job: Job, extra: { castHash?: string; overridesHash?: string; file?: string; settings?: ExportSettings } = {}): Project {
  const ref: JobRef = { jobId: job.jobId, status: job.status, finishedAt: job.finishedAt };
  switch (job.type) {
    case "analyze":
      return { ...p, jobs: { ...p.jobs, analyze: ref } };
    case "render":
      return { ...p, jobs: { ...p.jobs, render: { ...ref, castHash: extra.castHash ?? projectCastHash(p) } } };
    case "export": {
      const renderJobId = "renderJobId" in job.params ? job.params.renderJobId : undefined;
      const entry: ExportJobRef = { ...ref, overridesHash: extra.overridesHash ?? "", file: extra.file, renderJobId, settings: extra.settings };
      return { ...p, jobs: { ...p.jobs, exports: [...p.jobs.exports.filter((e) => e.jobId !== job.jobId), entry] } };
    }
  }
}

/** サーバーのジョブ状態を project.jobs に反映。変化がなければ同じオブジェクトを返す */
export function syncJobStatus(p: Project, job: Pick<Job, "jobId" | "status" | "finishedAt">): Project {
  const same = (r: JobRef | undefined) => !!r && r.jobId === job.jobId;
  const changed = (r: JobRef) => r.status !== job.status || r.finishedAt !== job.finishedAt;
  const upd = <T extends JobRef>(r: T): T => ({ ...r, status: job.status, finishedAt: job.finishedAt });
  const { analyze, render, exports } = p.jobs;
  if (same(analyze) && changed(analyze!)) return { ...p, jobs: { ...p.jobs, analyze: upd(analyze!) } };
  if (same(render) && changed(render!)) return { ...p, jobs: { ...p.jobs, render: upd(render!) } };
  const i = exports.findIndex((e) => e.jobId === job.jobId);
  if (i >= 0 && changed(exports[i]!)) {
    const next = [...exports];
    next[i] = upd(exports[i]!);
    return { ...p, jobs: { ...p.jobs, exports: next } };
  }
  return p;
}

export function setExportFile(p: Project, jobId: string, file: string): Project {
  return { ...p, jobs: { ...p.jobs, exports: p.jobs.exports.map((e) => (e.jobId === jobId ? { ...e, file } : e)) } };
}

/** サーバー側のデータが消えたとき：サーバーの ID とジョブを捨てて取り込みからやり直す */
export function resetServerState(p: Project): Project {
  return { ...p, compute: { profileId: p.compute.profileId }, jobs: { exports: [] }, meta: null };
}

export function allJobIds(p: Project): string[] {
  const ids: string[] = [];
  if (p.jobs.analyze) ids.push(p.jobs.analyze.jobId);
  if (p.jobs.render) ids.push(p.jobs.render.jobId);
  for (const e of p.jobs.exports) ids.push(e.jobId);
  return ids;
}

// --- 一覧表示用の段階 ---

/** use case が返す、プロジェクトへの変更（ストアは最新の状態に適用する） */
export type ProjectMutation = (p: Project) => Project;

export const composeMutations =
  (...fns: ProjectMutation[]): ProjectMutation =>
  (p) =>
    fns.reduce((acc, f) => f(acc), p);

export type ProjectPhase =
  | "draft"
  | "uploading"
  | "analyzing"
  | "analyze_failed"
  | "analyzed"
  | "rendering"
  | "render_failed"
  | "rendered"
  | "exported";

export function projectPhase(p: Project): ProjectPhase {
  const { analyze, render } = p.jobs;
  if (lastExport(p)) return "exported";
  if (render) {
    if (render.status === "succeeded") return "rendered";
    if (render.status === "failed" || render.status === "canceled") return "render_failed";
    return "rendering";
  }
  if (analyze) {
    if (analyze.status === "succeeded") return "analyzed";
    if (analyze.status === "failed" || analyze.status === "canceled") return "analyze_failed";
    return "analyzing";
  }
  if (p.compute.upload) return "uploading";
  return "draft";
}

// --- 名前 ---

/** 動画ファイル名から拡張子を除いた名前 */
export function nameFromSourcePath(path: string): string {
  const name = fileNameOf(path);
  const i = name.lastIndexOf(".");
  const base = (i > 0 ? name.slice(0, i) : name).trim();
  return base.replace(/[\\/:*?"<>|]/g, "_") || "untitled";
}

/** 重複しない名前（「名前」「名前 2」「名前 3」…） */
export function uniqueName(base: string, taken: Iterable<string>): string {
  const set = new Set(Array.from(taken, (t) => t.toLowerCase()));
  if (!set.has(base.toLowerCase())) return base;
  for (let n = 2; ; n++) {
    const cand = `${base} ${n}`;
    if (!set.has(cand.toLowerCase())) return cand;
  }
}

/** 書き出しファイル名「<名前>_001.mp4」 */
export function exportFileName(p: Project): string {
  const n = p.jobs.exports.filter((e) => e.status === "succeeded").length + 1;
  return `${p.name}_${String(n).padStart(3, "0")}.mp4`;
}
