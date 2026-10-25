// 重みの取得（#33）。サーバーの /v1/local/weights が返す状態。HF トークンはここには持たない（送るときだけ使い、残さない）

export type WeightFileState = "ok" | "unverified" | "partial" | "missing";

export interface WeightFile {
  dest: string;
  gated: boolean;
  license: string | null;
  /** HF のライセンス同意ページ（gated のとき） */
  licenseUrl?: string;
  state: WeightFileState;
  /** ディスク上のバイト数（partial は途中まで） */
  bytes: number;
}

export type WeightsJobState = "idle" | "running" | "succeeded" | "failed" | "canceled";

/** 利用者に案内を出すエラー（HF のアクセス許諾まわり）と、その他 */
export type WeightsErrorCode =
  | "HF_TOKEN_REQUIRED"
  | "HF_TOKEN_INVALID"
  | "HF_ACCESS_DENIED"
  | "WEIGHTS_DISK_FULL"
  | "WEIGHTS_NETWORK"
  | "WEIGHTS_NOT_FOUND"
  | "WEIGHTS_CHECKSUM_MISMATCH"
  | "WEIGHTS_IO_ERROR"
  | "WEIGHTS_FAILED";

export interface WeightsError {
  code: WeightsErrorCode | string;
  message: string;
  detail?: { repo?: string; url?: string } & Record<string, unknown>;
}

export interface WeightsJob {
  state: WeightsJobState;
  phase?: "preparing" | "checking" | "downloading" | "verifying" | "done" | "failed" | "canceled";
  fileIndex?: number;
  fileCount?: number;
  file?: string | null;
  bytesDone?: number;
  bytesTotal?: number | null;
  error?: WeightsError | null;
  /** manifest に sha256 が固定されていないファイルの、計算結果 */
  unpinned?: { dest: string; sha256: string }[];
}

export interface WeightsStatus {
  root: string;
  files: WeightFile[];
  allReady: boolean;
  freeBytes: number;
  needsToken: boolean;
  job: WeightsJob;
}

export const isWeightsRunning = (j: WeightsJob) => j.state === "running";

/** 全体の進み具合 0..1（ファイル数 + 今のファイルの割合）。分からなければ null */
export function weightsFraction(j: WeightsJob): number | null {
  if (j.state === "succeeded") return 1;
  if (j.state !== "running" || !j.fileCount) return null;
  const within = j.bytesTotal ? Math.min(1, (j.bytesDone ?? 0) / j.bytesTotal) : 0;
  return Math.min(1, ((j.fileIndex ?? 0) + within) / j.fileCount);
}
