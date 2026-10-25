// MediaProbePort の実装。Tauri は ffprobe（Rust 側）を第一候補、失敗したら <video> の loadedmetadata で代用（F02）

import { invoke } from "@tauri-apps/api/core";
import type { MediaProbePort } from "@/application/ports/media";
import type { LocalMediaInfo } from "@/domain/media";
import { normalizeCodec } from "@/domain/media";
import type { FileSystem } from "../fs/FileSystem";

/** <video> で長さと寸法だけ読む。コーデック・音声・回転はわからない（サーバーの ingest に任せる） */
export function probeWithVideoElement(
  url: string,
  timeoutMs = 5000,
): Promise<Pick<LocalMediaInfo, "durationSec" | "width" | "height">> {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    const done = (fn: () => void) => {
      clearTimeout(timer);
      v.removeAttribute("src");
      v.load();
      fn();
    };
    const timer = setTimeout(
      () => done(() => reject(new Error("動画のメタ情報を読めませんでした（時間切れ）"))),
      timeoutMs,
    );
    v.preload = "metadata";
    v.muted = true;
    v.onloadedmetadata = () => {
      // done() が src を外して読み直すと duration / videoWidth が NaN / 0 に戻るので、先に読む
      const m = { durationSec: v.duration, width: v.videoWidth, height: v.videoHeight };
      done(() => resolve(m));
    };
    v.onerror = () => done(() => reject(new Error("この動画はプレビューで読めません")));
    v.src = url;
  });
}

export class VideoElementMediaProbe implements MediaProbePort {
  constructor(private readonly fs: FileSystem) {}
  async probe(path: string): Promise<LocalMediaInfo> {
    const [m, stat] = await Promise.all([probeWithVideoElement(this.fs.toUrl(path)), this.fs.stat(path)]);
    // <video> の videoWidth / Height は回転適用後の寸法
    return { ...m, rotation: 0, codec: null, fps: null, hasAudio: null, sizeBytes: stat.size };
  }
}

interface RustProbe {
  durationSec: number;
  width: number;
  height: number;
  rotation: number;
  codec: string | null;
  fps: number | null;
  hasAudio: boolean;
  sizeBytes: number;
}

export class TauriMediaProbe implements MediaProbePort {
  private readonly fallback: VideoElementMediaProbe;
  constructor(fs: FileSystem) {
    this.fallback = new VideoElementMediaProbe(fs);
  }
  async probe(path: string): Promise<LocalMediaInfo> {
    try {
      const r = await invoke<RustProbe>("media_probe", { path });
      return { ...r, codec: normalizeCodec(r.codec) };
    } catch (e) {
      console.warn("[probe] ffprobe が使えないので <video> で代用します", e);
      return this.fallback.probe(path);
    }
  }
}
