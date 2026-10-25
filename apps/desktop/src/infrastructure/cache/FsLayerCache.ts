// LayerCachePort の実装（F05）。FileSystem 越しなので Tauri／フルモード／制限モードで共通。
// 取得済みの印は cache/<jobId>/.fetched/<name>.json（sha256 を記録）

import type { LayerCachePort } from "@/application/ports/cache";
import type { ComputeBackendPort, DownloadProgress } from "@/application/ports/compute";
import type { FileHasherPort } from "@/application/ports/media";
import { artifactLocalPath } from "@/domain/artifacts";
import { AppError } from "@/domain/errors";
import type { Artifact } from "@/domain/job";
import type { FileSystem } from "../fs/FileSystem";
import { joinPath } from "../fs/path";

const isSha256 = (s: string) => /^[0-9a-f]{64}$/i.test(s);

export class FsLayerCache implements LayerCachePort {
  private readonly inflight = new Map<string, Promise<string>>();

  constructor(
    private readonly fs: FileSystem,
    private readonly hasher?: FileHasherPort,
  ) {}

  private marker(cacheDir: string, name: string) {
    return joinPath(cacheDir, ".fetched", `${name}.json`);
  }

  localPath(cacheDir: string, artifact: Pick<Artifact, "name" | "kind">) {
    return joinPath(cacheDir, artifactLocalPath(artifact.name, artifact.kind));
  }

  async has(cacheDir: string, artifact: Artifact) {
    const m = this.marker(cacheDir, artifact.name);
    if (!(await this.fs.exists(m))) return false;
    try {
      return (JSON.parse(await this.fs.readText(m)) as { sha256?: string }).sha256 === artifact.sha256;
    } catch {
      return false;
    }
  }

  fetch(
    backend: ComputeBackendPort,
    cacheDir: string,
    jobId: string,
    artifact: Artifact,
    onProgress?: DownloadProgress,
  ) {
    const key = `${cacheDir}|${artifact.name}`;
    const running = this.inflight.get(key);
    if (running) return running;
    const p = this.doFetch(backend, cacheDir, jobId, artifact, onProgress).finally(() => this.inflight.delete(key));
    this.inflight.set(key, p);
    return p;
  }

  private async verify(path: string, artifact: Artifact) {
    if (!this.hasher || !isSha256(artifact.sha256)) return;
    const got = await this.hasher.sha256(path);
    if (got.toLowerCase() !== artifact.sha256.toLowerCase()) {
      await this.fs.remove(path);
      throw new AppError("CHECKSUM_MISMATCH", `成果物 ${artifact.name} が壊れています`, {
        detail: { expected: artifact.sha256, got },
      });
    }
  }

  private async doFetch(
    backend: ComputeBackendPort,
    cacheDir: string,
    jobId: string,
    artifact: Artifact,
    onProgress?: DownloadProgress,
  ) {
    const local = this.localPath(cacheDir, artifact);
    if (await this.has(cacheDir, artifact)) return local;
    await this.fs.ensureDir(cacheDir);
    if (artifact.kind === "zip") {
      const tmp = joinPath(cacheDir, ".download", `${artifact.name}.zip`);
      await backend.downloadArtifact(jobId, artifact.name, tmp, onProgress);
      await this.verify(tmp, artifact);
      await this.fs.unzip(tmp, cacheDir); // zip の中のパスは frames/… person_thumbs/… など（contracts §4）
      await this.fs.remove(tmp);
    } else {
      await backend.downloadArtifact(jobId, artifact.name, local, onProgress);
      await this.verify(local, artifact);
    }
    await this.fs.writeTextAtomic(
      this.marker(cacheDir, artifact.name),
      JSON.stringify({ sha256: artifact.sha256, size: artifact.size }),
    );
    return local;
  }
}
