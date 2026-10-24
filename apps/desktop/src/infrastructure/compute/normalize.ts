// サーバー応答の正規化。openapi.yaml（packages/contracts、レーン C）が入ったら生成型に合わせて締める

import type { Capabilities, Health, QueueItem, ServerProject } from "@/application/ports/compute";
import type { Artifact, Job } from "@/domain/job";

type Raw = Record<string, unknown>;
const isObj = (v: unknown): v is Raw => !!v && typeof v === "object" && !Array.isArray(v);
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

/** 配列そのもの、または { items: [...] } */
export function asList<T>(raw: unknown, key = "items"): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (isObj(raw) && Array.isArray(raw[key])) return raw[key] as T[];
  return [];
}

export function normalizeHealth(raw: unknown): Health {
  const r = isObj(raw) ? raw : {};
  return { status: "ok", version: typeof r.version === "string" ? r.version : "unknown", apiVersion: num(r.apiVersion) ?? 1 };
}

export function normalizeCapabilities(raw: unknown): Capabilities {
  const r = isObj(raw) ? raw : {};
  const gpu = isObj(r.gpu) ? r.gpu : Array.isArray(r.gpus) && isObj(r.gpus[0]) ? (r.gpus[0] as Raw) : {};
  const vramMb = num(gpu.vramMb) ?? num(r.vramMb) ?? (num(gpu.vramGb) !== undefined ? num(gpu.vramGb)! * 1024 : undefined);
  const limits = isObj(r.limits) ? r.limits : {};
  const queue = isObj(r.queue) ? r.queue : {};
  return {
    gpuName: typeof gpu.name === "string" ? gpu.name : typeof r.gpuName === "string" ? r.gpuName : null,
    vramMb: vramMb ?? null,
    limits: { maxDurationSec: num(limits.maxDurationSec), maxLongEdge: num(limits.maxLongEdge), maxTargets: num(limits.maxTargets) },
    queueLength: num(r.queueLength) ?? num(queue.length) ?? 0,
  };
}

export function normalizeServerProject(raw: unknown, projectId: string): ServerProject {
  const r = isObj(raw) ? raw : {};
  const media = asList<Raw>(r.media);
  return {
    projectId: typeof r.projectId === "string" ? r.projectId : projectId,
    mediaIds: media.length ? media.flatMap((m) => (typeof m.mediaId === "string" ? [m.mediaId] : [])) : asList<string>(r.mediaIds),
    jobs: asList<Job>(r.jobs),
  };
}

export function normalizeJob(raw: unknown): Job {
  const j = raw as Job;
  return { ...j, stages: Array.isArray(j.stages) ? j.stages : [], artifacts: Array.isArray(j.artifacts) ? j.artifacts : [] };
}

export const normalizeQueue = (raw: unknown): QueueItem[] => asList<QueueItem>(raw);
export const normalizeArtifacts = (raw: unknown): Artifact[] => asList<Artifact>(raw);
