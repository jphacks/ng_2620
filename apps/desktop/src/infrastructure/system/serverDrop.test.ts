// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { DropUploadEvent, FileDropEvent } from "@/application/ports/system";
import { MemoryFs } from "../fs/memoryFs";
import { safeFileName, serverFileDrop, sweepImports, uploadFileToServer } from "./serverDrop";

beforeAll(() => {
  // jsdom の Blob には arrayBuffer / text が無い（ブラウザにはある）
  const read = <T>(blob: Blob, how: "readAsArrayBuffer" | "readAsText") =>
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
});

afterEach(() => vi.restoreAllMocks());

const bytes = (n: number) => Uint8Array.from({ length: n }, (_, i) => (i * 7 + 3) & 0xff);
const fileOf = (name: string, n: number) => new File([bytes(n)], name, { type: "video/mp4" });

describe("safeFileName", () => {
  it("パスに使えない文字と先頭のドットを除く。空なら代わりの名前", () => {
    expect(safeFileName("動画 1.mp4")).toBe("動画 1.mp4");
    expect(safeFileName('a<b>c:d"e|f?g*h.mp4')).toBe("a_b_c_d_e_f_g_h.mp4");
    expect(safeFileName("../../etc/passwd")).toBe("passwd");
    expect(safeFileName("C:\\Users\\me\\x.mp4")).toBe("x.mp4");
    expect(safeFileName("..hidden")).toBe("hidden");
    expect(safeFileName("")).toBe("video");
    expect(safeFileName("\u0001\u0002")).toBe("__");
  });
});

describe("uploadFileToServer", () => {
  it("断片ごとに .part へ追記し、完了で dest に rename。進捗は断片ごと", async () => {
    const fs = new MemoryFs();
    const file = fileOf("a.mp4", 25);
    const seen: number[] = [];
    const orig = fs.appendBytes.bind(fs);
    const sizes: number[] = [];
    fs.appendBytes = async (p, b, e) => (sizes.push(b.length), orig(p, b, e));
    await uploadFileToServer(file, "/mem/imports/g/a.mp4", fs as never, {
      chunkSize: 10,
      onProgress: (s) => seen.push(s),
    });
    expect(sizes).toEqual([10, 10, 5]);
    expect(seen).toEqual([10, 20, 25]);
    expect(new Uint8Array(await fs.readBytes("/mem/imports/g/a.mp4"))).toEqual(bytes(25));
    expect(await fs.exists("/mem/imports/g/a.mp4.part")).toBe(false);
  });

  it("空のファイルも取り込める", async () => {
    const fs = new MemoryFs();
    await uploadFileToServer(fileOf("e.mp4", 0), "/mem/i/e.mp4", fs as never);
    expect((await fs.stat("/mem/i/e.mp4")).size).toBe(0);
  });

  it("中止すると .part を残さず、dest も作らない", async () => {
    const fs = new MemoryFs();
    const ctl = new AbortController();
    const p = uploadFileToServer(fileOf("a.mp4", 100), "/mem/i/a.mp4", fs as never, {
      chunkSize: 10,
      signal: ctl.signal,
      onProgress: (s) => s >= 30 && ctl.abort(),
    });
    await expect(p).rejects.toThrow();
    expect(await fs.exists("/mem/i/a.mp4")).toBe(false);
    expect(await fs.exists("/mem/i/a.mp4.part")).toBe(false);
  });

  it("サーバーが断片を拒否したら、.part を片付けて失敗する", async () => {
    const fs = new MemoryFs();
    const orig = fs.appendBytes.bind(fs);
    let n = 0;
    fs.appendBytes = async (p, b, e) => {
      if (++n === 3) throw new Error("disk full");
      return orig(p, b, e);
    };
    await expect(
      uploadFileToServer(fileOf("a.mp4", 100), "/mem/i/a.mp4", fs as never, { chunkSize: 10 }),
    ).rejects.toThrow("disk full");
    expect(await fs.exists("/mem/i/a.mp4.part")).toBe(false);
  });
});

describe("serverFileDrop", () => {
  function setup() {
    const fs = new MemoryFs();
    const uploads: DropUploadEvent[] = [];
    let n = 0;
    const port = serverFileDrop({
      fs,
      importsDir: async () => "/mem/AppData/imports",
      newId: () => `id${++n}`,
      onUpload: (ev) => uploads.push(ev),
    });
    const events: FileDropEvent[] = [];
    const off = port.subscribe((e) => events.push(e));
    const fire = (type: string, files: File[] = [], x = 5, y = 6) => {
      const e = new Event(type, { cancelable: true }) as Event & Record<string, unknown>;
      Object.assign(e, { clientX: x, clientY: y, relatedTarget: null, dataTransfer: { types: ["Files"], files } });
      window.dispatchEvent(e);
      return e;
    };
    return { fs, uploads, events, fire, off };
  }

  it("over／leave は座標だけ。ドロップするまで何も送らない", () => {
    const { events, uploads, fire, off } = setup();
    fire("dragover");
    fire("dragleave");
    expect(events).toEqual([{ type: "over", x: 5, y: 6, paths: [] }, { type: "leave" }]);
    expect(uploads).toEqual([]);
    off();
  });

  it("drop は取り込み先のパスと prepare を渡し、prepare() を呼ぶまで送らない", async () => {
    const { events, uploads, fs, fire, off } = setup();
    fire("drop", [fileOf("clip.mp4", 30)]);
    await vi.waitFor(() => expect(events).toHaveLength(1));
    const ev = events[0]!;
    if (ev.type !== "drop") throw new Error("drop ではありません");
    expect(ev.paths).toEqual(["/mem/AppData/imports/id1/clip.mp4"]);
    expect(uploads).toEqual([]);
    expect(await fs.exists(ev.paths[0]!)).toBe(false);

    const done = await ev.prepare!();
    expect(done).toEqual(["/mem/AppData/imports/id1/clip.mp4"]);
    expect(new Uint8Array(await fs.readBytes(done[0]!))).toEqual(bytes(30));
    expect(uploads.at(0)).toMatchObject({ state: "uploading", name: "clip.mp4", total: 30 });
    expect(uploads.at(-1)).toMatchObject({ state: "done", sent: 30 });
    off();
  });

  it("複数ファイルは同じ取り込みフォルダに入り、同じ名前でも衝突しない", async () => {
    const { events, fire, off } = setup();
    fire("drop", [fileOf("a.mp4", 5), fileOf("b.mov", 6)]);
    await vi.waitFor(() => expect(events).toHaveLength(1));
    const ev = events[0]!;
    if (ev.type !== "drop") throw new Error();
    expect(ev.paths).toEqual(["/mem/AppData/imports/id1/a.mp4", "/mem/AppData/imports/id1/b.mov"]);
    fire("drop", [fileOf("a.mp4", 5)]);
    await vi.waitFor(() => expect(events).toHaveLength(2));
    expect((events[1] as { paths: string[] }).paths[0]).toBe("/mem/AppData/imports/id4/a.mp4");
    off();
  });

  it("途中で中止したファイルは結果に含まれない（残りは取り込む）", async () => {
    const { events, uploads, fs, fire, off } = setup();
    fire("drop", [fileOf("a.mp4", 30), fileOf("b.mp4", 30)]);
    await vi.waitFor(() => expect(events).toHaveLength(1));
    const ev = events[0] as Extract<FileDropEvent, { type: "drop" }>;
    // 最初のファイルの転送が始まったら中止する
    const stop = setInterval(() => uploads.find((u) => u.state === "uploading" && u.name === "a.mp4")?.cancel?.(), 0);
    const orig = fs.appendBytes.bind(fs);
    fs.appendBytes = async (p, b, e) => {
      await new Promise((r) => setTimeout(r, 5));
      return orig(p, b, e);
    };
    const done = await ev.prepare!();
    clearInterval(stop);
    expect(done).toEqual(["/mem/AppData/imports/id1/b.mp4"]);
    expect(uploads.some((u) => u.name === "a.mp4" && u.state === "canceled")).toBe(true);
    off();
  });

  it("サーバーへ書けないとき error を通知し、結果には含めない", async () => {
    const { events, uploads, fs, fire, off } = setup();
    fs.appendBytes = async () => {
      throw new Error("PATH_NOT_ALLOWED");
    };
    fire("drop", [fileOf("a.mp4", 5)]);
    await vi.waitFor(() => expect(events).toHaveLength(1));
    expect(await (events[0] as Extract<FileDropEvent, { type: "drop" }>).prepare!()).toEqual([]);
    expect(uploads.at(-1)).toMatchObject({ state: "error", error: "PATH_NOT_ALLOWED" });
    off();
  });

  it("ファイル以外のドラッグ（テキストなど）は無視する", () => {
    const { events, fire, off } = setup();
    const e = new Event("dragover", { cancelable: true }) as Event & Record<string, unknown>;
    Object.assign(e, { clientX: 1, clientY: 1, dataTransfer: { types: ["text/plain"], files: [] } });
    window.dispatchEvent(e);
    fire("dragover", []);
    expect(events).toHaveLength(1); // 後者（Files）だけ
    off();
  });
});

describe("sweepImports", () => {
  const DAY = 24 * 60 * 60 * 1000;
  async function world() {
    const fs = new MemoryFs();
    const put = async (p: string) => fs.writeBytes(p, new Uint8Array([1]));
    return { fs, put };
  }

  it("参照されていない古い取り込みだけを消す", async () => {
    const { fs, put } = await world();
    await put("/mem/imports/old/a.mp4");
    await put("/mem/imports/used/b.mp4");
    await put("/mem/imports/fresh/c.mp4");
    const now = Date.now() + 2 * DAY;
    // fresh は『今』作ったことにするため、時刻を戻して書き直す
    vi.spyOn(Date, "now").mockReturnValue(now);
    await put("/mem/imports/fresh/d.mp4");
    const removed = await sweepImports(fs, "/mem/imports", ["/mem/imports/used/b.mp4"], now);
    expect(removed).toBe(1);
    expect(await fs.exists("/mem/imports/old")).toBe(false);
    expect(await fs.exists("/mem/imports/used/b.mp4")).toBe(true);
    expect(await fs.exists("/mem/imports/fresh/c.mp4")).toBe(true);
  });

  it("参照は Windows 区切りでも見分ける。取り込みフォルダが無くても何もしない", async () => {
    const { fs, put } = await world();
    await put("/mem/imports/g/a.mp4");
    expect(await sweepImports(fs, "/mem/imports", ["/mem/imports\\g\\a.mp4"], Date.now() + 3 * DAY)).toBe(0);
    expect(await sweepImports(fs, "/mem/none", [], Date.now() + 3 * DAY)).toBe(0);
  });

  it("中身が空の取り込みフォルダ（途中で失敗したもの）は新しくても消す", async () => {
    const { fs } = await world();
    await fs.ensureDir("/mem/imports/empty");
    expect(await sweepImports(fs, "/mem/imports", [], Date.now())).toBe(1);
    expect(await fs.exists("/mem/imports/empty")).toBe(false);
  });

  it("Windows のパスは大文字小文字と区切りの違いを無視して参照を見つける", async () => {
    const { fs } = await world();
    await fs.writeBytes("C:\\Data\\imports\\g\\a.mp4", new Uint8Array([1]));
    const removed = await sweepImports(fs, "C:\\Data\\imports", ["c:/data/IMPORTS/g/a.mp4"], Date.now() + 3 * DAY);
    expect(removed).toBe(0);
  });
});
