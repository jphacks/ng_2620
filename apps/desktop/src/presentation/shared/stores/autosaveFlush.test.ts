// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { installAutosaveFlush } from "./projectStore";

function setup(saveState: "dirty" | "saved" | "saving" | "error") {
  const saveNow = vi.fn(async () => {});
  const doc = new EventTarget() as unknown as Document & { visibilityState: DocumentVisibilityState };
  Object.defineProperty(doc, "visibilityState", { value: "visible", writable: true });
  const win = new EventTarget() as unknown as Window;
  const dispose = installAutosaveFlush({ getState: () => ({ saveState, saveNow }) }, doc, win);
  return { saveNow, doc, win, dispose };
}

describe("installAutosaveFlush", () => {
  it("タブが隠れる（閉じる・別のタブへ）とき、未保存なら保存する", () => {
    const { saveNow, doc } = setup("dirty");
    (doc as { visibilityState: string }).visibilityState = "hidden";
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(saveNow).toHaveBeenCalledTimes(1);
  });

  it("タブが見える状態に戻ったときは何もしない", () => {
    const { saveNow, doc } = setup("dirty");
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(saveNow).not.toHaveBeenCalled();
  });

  it("ページを離れる（pagehide）ときも保存する", () => {
    const { saveNow, win } = setup("dirty");
    win.dispatchEvent(new Event("pagehide"));
    expect(saveNow).toHaveBeenCalledTimes(1);
  });

  it("保存済みなら保存しない（無駄な書き込みをしない）", () => {
    const { saveNow, win } = setup("saved");
    win.dispatchEvent(new Event("pagehide"));
    expect(saveNow).not.toHaveBeenCalled();
  });

  it("dispose すると反応しない", () => {
    const { saveNow, win, dispose } = setup("dirty");
    dispose();
    win.dispatchEvent(new Event("pagehide"));
    expect(saveNow).not.toHaveBeenCalled();
  });
});
