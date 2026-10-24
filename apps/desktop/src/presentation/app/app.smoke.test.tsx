// @vitest-environment jsdom
// 画面のスモークテスト：ブラウザ版の組み立て（メモリ上のファイル＋シミュレーター）で、
// 新規 → 取り込み → 解析（段階的プレビュー）→ キャスト → 描画 → エディタ → 書き出し を画面の操作で通す（M1 相当）

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { webcrypto } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { ja } from "@/presentation/shared/i18n/ja";

// jsdom には canvas が無いので、シミュレーターの成果物は画像なし（中身は zip の目印だけ）にする
vi.mock("@/infrastructure/compute/simArtifacts", async (orig) => ({
  ...(await orig<typeof import("@/infrastructure/compute/simArtifacts")>()),
  canvasSynth: () => null,
}));

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.stubEnv("VITE_SIM_SPEED", "40");
  if (!globalThis.crypto?.subtle) Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
  URL.createObjectURL = () => "blob:mock";
  URL.revokeObjectURL = () => {};
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
  // jsdom の Blob には arrayBuffer / text が無い（ブラウザにはある）
  const read = <T,>(blob: Blob, how: "readAsArrayBuffer" | "readAsText") =>
    new Promise<T>((resolve, reject) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result as T);
      r.onerror = () => reject(r.error);
      r[how](blob);
    });
  Blob.prototype.arrayBuffer ??= function (this: Blob) {
    return read<ArrayBuffer>(this, "readAsArrayBuffer");
  };
  Blob.prototype.text ??= function (this: Blob) {
    return read<string>(this, "readAsText");
  };
  // モックサーバー（:8766）は立っていない
  globalThis.fetch = (() => Promise.reject(new TypeError("fetch failed"))) as typeof fetch;
});

afterAll(() => {
  cleanup();
  vi.unstubAllEnvs();
});

const BONES = [
  "hips",
  "spine",
  "head",
  "leftUpperArm",
  "leftLowerArm",
  "leftHand",
  "rightUpperArm",
  "rightLowerArm",
  "rightHand",
].concat(["leftUpperLeg", "leftLowerLeg", "leftFoot", "rightUpperLeg", "rightLowerLeg", "rightFoot"]);

/** 必須ボーンだけを持つ最小の VRM 1.0（GLB） */
function minimalVrm(name: string): Uint8Array {
  const humanBones = Object.fromEntries(BONES.map((b, i) => [b, { node: i }]));
  const json = {
    asset: { version: "2.0" },
    extensions: {
      VRMC_vrm: { meta: { name, authors: ["Tester"], commercialUsage: "corporation" }, humanoid: { humanBones } },
    },
  };
  let j = new TextEncoder().encode(JSON.stringify(json));
  j = new Uint8Array([...j, ...new Array((4 - (j.length % 4)) % 4).fill(0x20)]);
  const out = new Uint8Array(20 + j.length);
  const v = new DataView(out.buffer);
  v.setUint32(0, 0x46546c67, true);
  v.setUint32(4, 2, true);
  v.setUint32(8, out.length, true);
  v.setUint32(12, j.length, true);
  v.setUint32(16, 0x4e4f534a, true);
  out.set(j, 20);
  return out;
}

const enabled = (name: RegExp) => expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(false);
const go = (hash: string) =>
  act(() => {
    window.location.hash = hash;
  });

describe("アプリ全体（シミュレーター）", () => {
  it("ホーム → 取り込み → 解析 → キャスト → 描画 → エディタ → 書き出し", async () => {
    const { createServices } = await import("./container");
    const { AppRoot } = await import("./AppRoot");
    const { useConnectionStore } = await import("@/presentation/shared/stores/connectionStore");
    const { useProjectStore } = await import("@/presentation/shared/stores/projectStore");

    const services = createServices();
    render(<AppRoot services={services} />);
    await screen.findByText(ja.home.dropTitle);

    // 設定の各タブが開ける
    await go("#/settings/connection");
    await screen.findByText(ja.connection.profiles);
    await go("#/settings/general");
    await screen.findByText(ja.general.diagnostics);
    await go("#/");

    // 開発用シミュレーターに接続
    await act(() => useConnectionStore.getState().select("dev-memory"));
    await waitFor(() => expect(useConnectionStore.getState().state.status).toBe("connected"));

    // VRM をライブラリへ
    await services.platform.files.writeBytes("/mem/drops/v/a.vrm", minimalVrm("Sample A"));
    const imported = await services.uc.importAvatar.execute(["/mem/drops/v/a.vrm"]);
    expect(imported.failed).toEqual([]);

    // 動画からプロジェクトを作る（中身は何でもよい：シミュレーターはそれを書き出し結果として返す）
    const src = "/mem/drops/t/clip.mp4";
    await services.platform.files.writeBytes(src, new Uint8Array(1000).fill(7));
    const project = await act(() =>
      useProjectStore.getState().createFrom(src, {
        durationSec: 2,
        width: 540,
        height: 960,
        rotation: 0,
        codec: "h264",
        fps: 30,
        hasAudio: false,
        sizeBytes: 1000,
      }),
    );
    expect(project.name).toBe("clip");
    await go(`#/project/${project.id}/import`);

    // S2-1 → S2-2：アップロード → 解析
    await waitFor(() => enabled(/解析を始める/));
    fireEvent.click(screen.getByRole("button", { name: /解析を始める/ }));
    await waitFor(() => enabled(/人物を選ぶ/), { timeout: 10_000 });
    expect(useProjectStore.getState().current?.project.compute.mediaId).toBeTruthy();

    // S2-3：人物 1 を選び、アバターを割り当てて描画
    fireEvent.click(screen.getByRole("button", { name: /人物を選ぶ/ }));
    await screen.findByText("人物 1", undefined, { timeout: 5_000 });
    fireEvent.click(screen.getAllByRole("checkbox")[0]!);
    fireEvent.click(await screen.findByRole("button", { name: /アバターを選ぶ/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Sample A/ }));
    await waitFor(() => enabled(/アバターを描画/));
    fireEvent.click(screen.getByRole("button", { name: /アバターを描画/ }));

    // S2-4 → 描画が終わると自動でエディタへ
    await waitFor(() => expect(window.location.hash).toBe(`#/project/${project.id}/edit`), { timeout: 10_000 });
    await screen.findByText(ja.editor.view.composite); // エディタ（レーン B）の表示モード切替
    const rendered = useProjectStore.getState().current!.project;
    expect(rendered.cast).toEqual([{ personId: 1, action: "replace", avatarId: imported.imported[0]!.avatarId }]);
    expect(rendered.jobs.render?.status).toBe("succeeded");

    // S4：書き出し → 保存先に MP4
    await go(`#/project/${project.id}/export`);
    await waitFor(() => enabled(/書き出す/));
    fireEvent.click(screen.getByRole("button", { name: /書き出す/ }));
    await screen.findByText(/完了:/, undefined, { timeout: 10_000 });
    const done = useProjectStore.getState().current!.project;
    expect(done.jobs.exports[0]).toMatchObject({ status: "succeeded", file: "exports/clip_001.mp4" });
    expect(await services.platform.files.exists(services.paths.resolve(done.id, "exports/clip_001.mp4"))).toBe(true);

    // 自動保存されている
    await act(() => useProjectStore.getState().saveNow());
    expect(useProjectStore.getState().saveState).toBe("saved");
  }, 30_000);
});
