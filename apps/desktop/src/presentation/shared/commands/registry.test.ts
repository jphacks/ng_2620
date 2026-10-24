import { describe, expect, it } from "vitest";
import { CommandRegistry, comboFromEvent, DuplicateShortcutError, normalizeCombo } from "./registry";

const noop = () => {};

describe("normalizeCombo", () => {
  it("修飾キーの順と別名を揃える", () => {
    expect(normalizeCombo("Shift+Mod+Z", true)).toBe("mod+shift+z");
    expect(normalizeCombo("cmd+,", true)).toBe("mod+,");
    expect(normalizeCombo("ctrl+j", true)).toBe("ctrl+j");
    expect(normalizeCombo("ctrl+j", false)).toBe("mod+j"); // Windows では ⌃ = Ctrl = mod
    expect(normalizeCombo("mod++", true)).toBe("mod++");
    expect(normalizeCombo("ArrowLeft", true)).toBe("left");
  });

  it("キー入力から同じ表記を作る", () => {
    const ev = { key: "Z", code: "KeyZ", metaKey: true, ctrlKey: false, altKey: false, shiftKey: true };
    expect(comboFromEvent(ev, true)).toBe("mod+shift+z");
    expect(comboFromEvent({ ...ev, key: "!", code: "Digit1", shiftKey: false }, true)).toBe("mod+1");
    expect(
      comboFromEvent({ key: "j", code: "KeyJ", metaKey: false, ctrlKey: true, altKey: false, shiftKey: false }, true),
    ).toBe("ctrl+j");
    expect(
      comboFromEvent({ key: "j", code: "KeyJ", metaKey: false, ctrlKey: true, altKey: false, shiftKey: false }, false),
    ).toBe("mod+j");
  });
});

describe("CommandRegistry", () => {
  it("同じ範囲・重なる範囲の重複キーは登録できない", () => {
    const r = new CommandRegistry(true);
    r.register({ id: "save", keys: ["mod+s"], scope: "global", run: noop });
    expect(() => r.register({ id: "other", keys: ["Mod+S"], scope: "workspace:edit", run: noop })).toThrow(
      DuplicateShortcutError,
    );
    r.register({ id: "tab1", keys: ["mod+1"], scope: "project", run: noop });
    expect(() => r.register({ id: "x", keys: ["mod+1"], scope: "workspace:export", run: noop })).toThrow(
      DuplicateShortcutError,
    );
  });

  it("別々のワークスペースでは同じキーを使える。有効なものだけ解決される", () => {
    const r = new CommandRegistry(true);
    const hits: string[] = [];
    r.register({ id: "edit.front", keys: ["f"], scope: "workspace:edit", run: () => hits.push("edit") });
    r.register({ id: "export.f", keys: ["f"], scope: "workspace:export", run: () => hits.push("export") });
    r.register({ id: "jobs", keys: ["ctrl+j"], scope: "global", run: () => hits.push("jobs") });

    expect(r.resolve("f", { projectOpen: true, workspace: "edit" })?.id).toBe("edit.front");
    expect(r.resolve("f", { projectOpen: true, workspace: "export" })?.id).toBe("export.f");
    expect(r.resolve("f", { projectOpen: true, workspace: "import" })).toBeNull();
    expect(r.resolve("f", { projectOpen: false, workspace: null })).toBeNull();
    expect(r.resolve("ctrl+j", { projectOpen: false, workspace: null })?.id).toBe("jobs");
  });

  it("登録解除と、同じ ID の登録し直し", () => {
    const r = new CommandRegistry(false);
    const off = r.register({ id: "a", keys: ["mod+e"], scope: "project", run: noop });
    r.register({ id: "a", keys: ["mod+e"], scope: "project", run: noop }); // 同じ ID は置き換え
    off(); // 古い登録の解除は新しい登録を消さない
    expect(r.resolve("mod+e", { projectOpen: true, workspace: "edit" })?.id).toBe("a");
    expect(r.list()).toHaveLength(1);
  });
});
