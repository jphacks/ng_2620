// e2e/strings.ts（Playwright が画面の文言で要素を探すための写し）が、ja.ts とずれていないこと
import { describe, expect, it } from "vitest";
import { ja as e2e, VRM_BONES } from "../e2e/strings";
import { REQUIRED_BONES } from "./infrastructure/avatar/vrmMeta";
import { ja } from "./presentation/shared/i18n/ja";

function check(copy: Record<string, unknown>, source: Record<string, unknown>, path = "ja") {
  for (const [k, v] of Object.entries(copy)) {
    const real = source[k];
    if (v && typeof v === "object") {
      expect(real, `${path}.${k}`).toBeTypeOf("object");
      check(v as Record<string, unknown>, real as Record<string, unknown>, `${path}.${k}`);
    } else {
      expect(real, `${path}.${k}`).toBe(v);
    }
  }
}

describe("e2e の文言の写し", () => {
  it("VRM の必須ボーンが画面側と同じ", () => {
    expect(VRM_BONES).toEqual([...REQUIRED_BONES]);
  });

  it("ja.ts と同じ", () => {
    check(e2e, ja as unknown as Record<string, unknown>);
  });
});
