// @vitest-environment jsdom
// ドロップ先の振り分け：prepare を持つドロップ（ブラウザのフルモード）は、受け付ける先でだけ取り込みを始める

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { FileDropEvent, FileDropPort } from "@/application/ports/system";
import type { Services } from "../services";
import { ServicesProvider } from "../services";
import { useDropZone } from "./useDropZone";

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(cleanup);

// useDropZone はポートを最初の 1 回だけ購読する（モジュール内の状態）ので、テスト全体で同じポートを使う
let emitToHook: (ev: FileDropEvent) => void = () => {};
const sharedPort: FileDropPort = {
  subscribe(l) {
    emitToHook = l;
    return () => {};
  },
};

function setup(opts: { accept?: (p: string) => boolean } = {}) {
  const services = { platform: { drops: sharedPort } } as unknown as Services;
  const onDrop = vi.fn();
  function Zone() {
    const z = useDropZone({ accept: opts.accept ?? ((p) => p.endsWith(".mp4")), onDrop });
    return <div data-testid="zone" {...z.props} />;
  }
  const r = render(
    <ServicesProvider services={services}>
      <Zone />
    </ServicesProvider>,
  );
  const el = r.getByTestId("zone");
  document.elementFromPoint = () => el;
  const emit = (ev: FileDropEvent) => act(async () => emitToHook(ev));
  /** 『この Zone がドロップを受けられる状態か』の対照：受け付ける従来のドロップを 1 回流して onDrop が呼ばれること */
  const control = async () => {
    await emit({ type: "drop", x: 1, y: 1, paths: ["/control.mp4"] });
    expect(onDrop).toHaveBeenCalledWith(["/control.mp4"]);
    onDrop.mockClear();
  };
  return { emit, onDrop, control };
}

describe("useDropZone と prepare", () => {
  it("受け付けるドロップは prepare を呼び、できたパスを onDrop に渡す", async () => {
    const { emit, onDrop, control } = setup();
    await control();
    const prepare = vi.fn(async () => ["/srv/imports/g/a.mp4"]);
    await emit({ type: "drop", x: 1, y: 1, paths: ["/srv/imports/g/a.mp4"], prepare });
    expect(prepare).toHaveBeenCalledTimes(1);
    expect(onDrop).toHaveBeenCalledWith(["/srv/imports/g/a.mp4"]);
  });

  it("受け付けない拡張子では prepare を呼ばない（何も送らない）", async () => {
    const { emit, onDrop, control } = setup();
    await control();
    const prepare = vi.fn(async () => ["/x/a.txt"]);
    await emit({ type: "drop", x: 1, y: 1, paths: ["/x/a.txt"], prepare });
    expect(prepare).not.toHaveBeenCalled();
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("prepare が空（全部失敗・中止）なら onDrop を呼ばない", async () => {
    const { emit, onDrop, control } = setup();
    await control();
    const prepare = vi.fn(async () => []);
    await emit({ type: "drop", x: 1, y: 1, paths: ["/x/a.mp4"], prepare });
    expect(prepare).toHaveBeenCalledTimes(1);
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("prepare が失敗しても落ちない（表示は取り込み側）", async () => {
    const { emit, onDrop, control } = setup();
    await control();
    const prepare = vi.fn(async () => {
      throw new Error("boom");
    });
    await emit({ type: "drop", x: 1, y: 1, paths: ["/x/a.mp4"], prepare });
    expect(prepare).toHaveBeenCalledTimes(1);
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("prepare が無い従来のドロップ（制限モード）はそのまま渡す", async () => {
    const { emit, onDrop } = setup();
    await emit({ type: "drop", x: 1, y: 1, paths: ["/mem/drops/1/a.mp4"] });
    expect(onDrop).toHaveBeenCalledWith(["/mem/drops/1/a.mp4"]);
  });
});
