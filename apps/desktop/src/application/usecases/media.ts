// F02 動画の取り込み・検証・アップロード（中断からの再開を含む）

import { AppError, isAppError } from "@/domain/errors";
import type { LocalMediaInfo, MediaMeta } from "@/domain/media";
import type { Project, ProjectMutation } from "@/domain/project";
import { chunkCount, chunkRange, DEFAULT_CHUNK_SIZE, missingChunks, receivedBytes, UPLOAD_CONCURRENCY } from "@/domain/upload";
import type { MediaLimits, Violation } from "@/domain/validation";
import { DEFAULT_LIMITS, fileNameOf, isAcceptedVideoPath, validateMedia } from "@/domain/validation";
import type { ComputeBackendPort } from "../ports/compute";
import type { FileHasherPort, LocalFilePort, MediaProbePort } from "../ports/media";
import type { ClockPort } from "../ports/system";

export class ProbeMedia {
  constructor(private readonly d: { probe: MediaProbePort }) {}

  async execute(path: string, limits: MediaLimits = DEFAULT_LIMITS): Promise<{ info: LocalMediaInfo | null; violations: Violation[] }> {
    if (!isAcceptedVideoPath(path)) return { info: null, violations: validateMedia(path, null, limits) };
    const info = await this.d.probe.probe(path);
    return { info, violations: validateMedia(path, info, limits) };
  }
}

export type ImportPhase = "creating" | "hashing" | "uploading" | "finalizing";

export interface ImportProgress {
  phase: ImportPhase;
  ratio: number; // その段階の 0..1
  sentBytes?: number;
  totalBytes?: number;
  bytesPerSec?: number;
}

/** 並列数を絞って順に処理する */
async function runPool<T>(items: readonly T[], concurrency: number, fn: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++]!;
      await fn(item);
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
}

/**
 * サーバー側プロジェクト作成 → sha256 → 分割アップロード（8MB・並列 3）→ complete。
 * project.compute.upload があれば受信済みの断片を確かめて残りだけ送る（ResumeUpload を兼ねる）。
 * 途中の ID は onCheckpoint で逐次プロジェクトに記録する（アプリを閉じても再開できるように）。
 */
export class ImportMedia {
  constructor(private readonly d: { files: LocalFilePort; hasher: FileHasherPort; clock: ClockPort }) {}

  async execute(input: {
    project: Project;
    backend: ComputeBackendPort;
    onProgress?: (p: ImportProgress) => void;
    onCheckpoint?: (apply: ProjectMutation) => void;
    signal?: AbortSignal;
  }): Promise<{ apply: ProjectMutation; mediaId: string; meta: MediaMeta }> {
    const { files, hasher, clock } = this.d;
    const { backend, signal } = input;
    const progress = input.onProgress ?? (() => {});
    const checkpoint = input.onCheckpoint ?? (() => {});
    const path = input.project.source.path;
    let p = input.project;
    const commit = (fn: ProjectMutation) => {
      p = fn(p);
      checkpoint(fn);
    };

    // 1. サーバー側プロジェクト
    let serverProjectId = p.compute.serverProjectId;
    if (!serverProjectId) {
      progress({ phase: "creating", ratio: 0 });
      serverProjectId = (await backend.createProject(p.name)).projectId;
      const id = serverProjectId;
      commit((x) => ({ ...x, compute: { ...x.compute, profileId: backend.profileId ?? x.compute.profileId, serverProjectId: id } }));
    }
    signal?.throwIfAborted();

    // 2. sha256（計算済みなら再利用）
    let sha256 = p.source.sha256;
    if (!sha256) {
      progress({ phase: "hashing", ratio: 0 });
      sha256 = await hasher.sha256(path, (ratio) => progress({ phase: "hashing", ratio }));
      const h = sha256;
      commit((x) => ({ ...x, source: { ...x.source, sha256: h } }));
    }
    signal?.throwIfAborted();
    const { size } = await files.stat(path);

    // 3. アップロード（再開できるなら残りだけ）
    let upload = p.compute.upload;
    let received: number[] = [];
    if (upload) {
      try {
        received = (await backend.getUpload(upload.uploadId)).receivedChunks;
      } catch (e) {
        if (!isAppError(e, "NOT_FOUND")) throw e;
        upload = undefined; // サーバー側で消えている → 最初から
      }
    }
    if (!upload) {
      const r = await backend.createUpload({ projectId: serverProjectId, kind: "source", filename: fileNameOf(path), size, sha256 });
      upload = { uploadId: r.uploadId, chunkSize: r.chunkSize || DEFAULT_CHUNK_SIZE };
      const u = upload;
      commit((x) => ({ ...x, compute: { ...x.compute, upload: u } }));
    }

    const { uploadId, chunkSize } = upload;
    const sendMissing = async (already: number[]) => {
      const total = chunkCount(size, chunkSize);
      const todo = missingChunks(total, already);
      let sent = receivedBytes(already, size, chunkSize);
      const startedAt = clock.now().getTime();
      const startBytes = sent;
      progress({ phase: "uploading", ratio: size ? sent / size : 1, sentBytes: sent, totalBytes: size });
      await runPool(todo, UPLOAD_CONCURRENCY, async (index) => {
        signal?.throwIfAborted();
        const { offset, length } = chunkRange(index, size, chunkSize);
        await backend.uploadChunk(uploadId, index, { path, offset, length });
        sent += length;
        const elapsed = (clock.now().getTime() - startedAt) / 1000;
        progress({
          phase: "uploading",
          ratio: size ? sent / size : 1,
          sentBytes: sent,
          totalBytes: size,
          bytesPerSec: elapsed > 0 ? (sent - startBytes) / elapsed : undefined,
        });
      });
    };
    await sendMissing(received);

    // 4. 結合・検証。断片が欠けていたら欠けた分だけ送り直して 1 回だけやり直す
    progress({ phase: "finalizing", ratio: 0 });
    let result: { mediaId: string; meta: MediaMeta };
    try {
      result = await backend.completeUpload(uploadId);
    } catch (e) {
      if (isAppError(e, "UPLOAD_INCOMPLETE")) {
        await sendMissing((await backend.getUpload(uploadId)).receivedChunks);
      } else if (isAppError(e, "CHECKSUM_MISMATCH") && Array.isArray(e.detail?.badChunks)) {
        const bad = new Set(e.detail.badChunks as number[]);
        const all = Array.from({ length: chunkCount(size, chunkSize) }, (_, i) => i);
        await sendMissing(all.filter((i) => !bad.has(i)));
      } else {
        throw e;
      }
      result = await backend.completeUpload(uploadId);
    }
    if (!result.mediaId) throw new AppError("UNKNOWN", "サーバーが mediaId を返しませんでした");

    const { mediaId, meta } = result;
    const apply: ProjectMutation = (x) => ({ ...x, meta, compute: { ...x.compute, mediaId, upload: undefined } });
    progress({ phase: "finalizing", ratio: 1 });
    return { apply, mediaId, meta };
  }
}
