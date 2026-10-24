import { describe, expect, it } from "vitest";
import type { LocalMediaInfo } from "./media";
import { displaySize } from "./media";
import { isAcceptedVideoPath, mergeLimits, validateMedia, DEFAULT_LIMITS } from "./validation";

const info = (o: Partial<LocalMediaInfo> = {}): LocalMediaInfo => ({
  durationSec: 9.8,
  width: 1920,
  height: 1080,
  rotation: 0,
  codec: "h264",
  fps: 29.97,
  hasAudio: true,
  sizeBytes: 20_000_000,
  ...o,
});

describe("validateMedia", () => {
  it("30.0 秒・長辺 1920px ちょうどは通す", () => {
    expect(validateMedia("a.mp4", info({ durationSec: 30.0 }))).toEqual([]);
    expect(validateMedia("a.mp4", info({ durationSec: 30.004 }))).toEqual([]); // コンテナの端数
  });

  it("30 秒超は MEDIA_TOO_LONG", () => {
    expect(validateMedia("a.mp4", info({ durationSec: 30.1 }))).toEqual([
      { code: "MEDIA_TOO_LONG", durationSec: 30.1, limitSec: 30 },
    ]);
  });

  it("長辺 1920px 超は MEDIA_TOO_LARGE（縦動画も長辺で判定）", () => {
    expect(validateMedia("a.mov", info({ width: 3840, height: 2160 }))[0]?.code).toBe("MEDIA_TOO_LARGE");
    expect(validateMedia("a.mov", info({ width: 1080, height: 1920, rotation: 90 }))).toEqual([]);
    expect(validateMedia("a.mov", info({ width: 1080, height: 1921 }))[0]?.code).toBe("MEDIA_TOO_LARGE");
  });

  it("拡張子とコーデック", () => {
    expect(validateMedia("a.avi", info())).toEqual([{ code: "MEDIA_UNSUPPORTED", reason: "extension", value: "avi" }]);
    expect(validateMedia("a.MOV", info({ codec: "hvc1" }))).toEqual([]);
    expect(validateMedia("a.mp4", info({ codec: "vp9" }))).toEqual([
      { code: "MEDIA_UNSUPPORTED", reason: "codec", value: "vp9" },
    ]);
    // コーデックが読めないときはサーバーの ingest に任せる
    expect(validateMedia("a.mp4", info({ codec: null }))).toEqual([]);
    expect(isAcceptedVideoPath("C:\\x\\clip.MP4")).toBe(true);
    expect(isAcceptedVideoPath("clip.vrm")).toBe(false);
  });

  it("サーバーの上限で上書きできる", () => {
    const limits = mergeLimits(DEFAULT_LIMITS, { maxDurationSec: 10 });
    expect(validateMedia("a.mp4", info({ durationSec: 12 }), limits)[0]?.code).toBe("MEDIA_TOO_LONG");
  });
});

describe("displaySize", () => {
  it("90 度回転で幅と高さを入れ替える", () => {
    expect(displaySize({ width: 1920, height: 1080, rotation: 90 })).toEqual({ width: 1080, height: 1920 });
    expect(displaySize({ width: 1920, height: 1080, rotation: -90 })).toEqual({ width: 1080, height: 1920 });
    expect(displaySize({ width: 1920, height: 1080, rotation: 180 })).toEqual({ width: 1920, height: 1080 });
  });
});
