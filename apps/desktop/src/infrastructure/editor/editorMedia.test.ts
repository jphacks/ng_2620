// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { encode } from "fast-png";
import { describe, expect, it } from "vitest";
import { decodeLabels } from "./editorMedia";

describe("decodeLabels", () => {
  it("16bit PNG の値をそのまま返す（8bit に丸めない）", () => {
    const data = new Uint16Array([0, 1, 300, 65535]);
    const png = encode({ width: 2, height: 2, data, depth: 16, channels: 1 });
    expect(Array.from(decodeLabels(png).data)).toEqual([0, 1, 300, 65535]);
  });

  it("フィクスチャ v0 の units（Pillow が書いた 16bit PNG）を読める", () => {
    const p = join(__dirname, "../../../../../fixtures/synth/synth.vvlog/cache/job_synth_analyze/units/00048.png");
    const l = decodeLabels(new Uint8Array(readFileSync(p)));
    expect([l.width, l.height]).toEqual([540, 960]);
    expect(new Set(l.data)).toEqual(new Set([0, 1, 2]));
    expect(l.data[600 * 540 + 100]).toBe(1); // ユニット 1 の内側（x 60..260, y 520..720）
  });
});
