// LayerCachePort：成果物をプロジェクトの cache/<jobId>/ に取り寄せる（F05）。zip は展開する

import type { Artifact } from "@/domain/job";
import type { ComputeBackendPort, DownloadProgress } from "./compute";

export interface LayerCachePort {
  /** 取得済み（sha256 が一致する）か */
  has(cacheDir: string, artifact: Artifact): Promise<boolean>;
  /** 取得して展開し、ローカルのパス（json / video はファイル、zip は展開先）を返す。済みなら何もしない */
  fetch(backend: ComputeBackendPort, cacheDir: string, jobId: string, artifact: Artifact, onProgress?: DownloadProgress): Promise<string>;
  /** 取得後のローカルのパス */
  localPath(cacheDir: string, artifact: Pick<Artifact, "name" | "kind">): string;
}
