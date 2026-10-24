// 動画のメタ情報

/** サーバーの ingest が決めるメタ情報（contracts §2.2、ADR-0011）。後段はこれだけを見る */
export interface MediaMeta {
  durationSec: number;
  frameCount: number;
  fps: number; // CFR 化後
  full: { width: number; height: number }; // 回転適用後
  proxy: { width: number; height: number };
  proxyScale: number; // full = proxy * proxyScale（整数）
  hasAudio: boolean;
  sourceCodec: string;
}

/** アプリ側の事前検証用（ffprobe か <video> で読む）。読めなかった項目は null */
export interface LocalMediaInfo {
  durationSec: number;
  width: number; // コンテナに書かれた幅（回転前）
  height: number;
  rotation: number; // 0 / 90 / 180 / 270
  codec: string | null; // "h264" | "hevc" | ...
  fps: number | null;
  hasAudio: boolean | null;
  sizeBytes: number;
}

/** 回転を適用した表示上の寸法 */
export function displaySize(info: Pick<LocalMediaInfo, "width" | "height" | "rotation">): {
  width: number;
  height: number;
} {
  const r = ((info.rotation % 360) + 360) % 360;
  return r === 90 || r === 270
    ? { width: info.height, height: info.width }
    : { width: info.width, height: info.height };
}

export function normalizeCodec(codec: string | null | undefined): string | null {
  if (!codec) return null;
  const c = codec.toLowerCase();
  if (c === "avc1" || c === "avc" || c === "h264") return "h264";
  if (c === "hvc1" || c === "hev1" || c === "h265" || c === "hevc") return "hevc";
  return c;
}
