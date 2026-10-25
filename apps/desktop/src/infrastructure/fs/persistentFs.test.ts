// IndexedDB は fake-indexeddb。Blob は Node 自前のもの（jsdom の Blob は構造化複製できない＝本物のブラウザと違う）を使う
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it } from "vitest";
import { AppError } from "@/domain/errors";
import { MemoryFs } from "./memoryFs";
import { PersistentFs } from "./persistentFs";
import { RoutingFs } from "./routingFs";
import { writeStoreZip } from "./storeZip";

const fresh = () => new IDBFactory();
const bytes = (n: number, k = 1) => Uint8Array.from({ length: n }, (_, i) => (i * k) & 0xff);

describe("PersistentFs", () => {
  it("書いたものが、開き直しても残る（テキスト・バイト列・フォルダ）", async () => {
    const idb = fresh();
    const a = await PersistentFs.open("t", idb);
    await a.writeTextAtomic("/mem/Movies/vvlog/x.vvlog/project.json", '{"名前":"テスト"}');
    await a.writeBytes("/mem/Movies/vvlog/x.vvlog/cache/a.bin", bytes(1000, 3));
    await a.ensureDir("/mem/Movies/vvlog/empty");
    a.close();

    const b = await PersistentFs.open("t", idb);
    expect(await b.readText("/mem/Movies/vvlog/x.vvlog/project.json")).toBe('{"名前":"テスト"}');
    expect(new Uint8Array(await b.readBytes("/mem/Movies/vvlog/x.vvlog/cache/a.bin"))).toEqual(bytes(1000, 3));
    expect((await b.stat("/mem/Movies/vvlog/x.vvlog/cache/a.bin")).size).toBe(1000);
    expect(await b.exists("/mem/Movies/vvlog/empty")).toBe(true);
    expect((await b.listDir("/mem/Movies/vvlog")).sort()).toEqual(["empty", "x.vvlog"]);
    b.close();
  });

  it("上書き・追記・rename・copy・unzip が残る", async () => {
    const idb = fresh();
    const a = await PersistentFs.open("t", idb);
    await a.writeTextAtomic("/p/a.txt", "v1");
    await a.writeTextAtomic("/p/a.txt", "v2");
    await a.appendBytes("/p/d.part", new Uint8Array([1, 2]), 0);
    await a.appendBytes("/p/d.part", new Uint8Array([3]), 2);
    await a.rename("/p/d.part", "/p/d.bin");
    await a.copyFile("/p/a.txt", "/p/sub/b.txt");
    await a.writeBytes("/p/z.zip", writeStoreZip([{ name: "frames/1.webp", bytes: new Uint8Array([9]) }]));
    await a.unzip("/p/z.zip", "/p/cache");
    a.close();

    const b = await PersistentFs.open("t", idb);
    expect(await b.readText("/p/a.txt")).toBe("v2");
    expect(new Uint8Array(await b.readBytes("/p/d.bin"))).toEqual(new Uint8Array([1, 2, 3]));
    expect(await b.exists("/p/d.part")).toBe(false);
    expect(await b.readText("/p/sub/b.txt")).toBe("v2");
    expect(new Uint8Array(await b.readBytes("/p/cache/frames/1.webp"))).toEqual(new Uint8Array([9]));
    b.close();
  });

  it("消したものは開き直しても戻らない（フォルダごと・ゴミ箱）", async () => {
    const idb = fresh();
    const a = await PersistentFs.open("t", idb);
    await a.writeTextAtomic("/p/x/1.txt", "1");
    await a.writeTextAtomic("/p/x/y/2.txt", "2");
    await a.writeTextAtomic("/p/keep.txt", "k");
    await a.ensureDir("/p/x/emptydir");
    await a.remove("/p/x");
    await a.moveToTrash("/p/keep.txt");
    a.close();
    const b = await PersistentFs.open("t", idb);
    expect(await b.exists("/p/x")).toBe(false);
    expect(await b.exists("/p/keep.txt")).toBe(false);
    expect(await b.listDir("/p")).toEqual([]);
    b.close();
  });

  it("同期の toUrl が、保存済みのものにも使える（開き直した後でも）", async () => {
    const idb = fresh();
    const a = await PersistentFs.open("t", idb);
    await a.writeBytes("/p/thumb.png", bytes(10));
    a.close();
    const b = await PersistentFs.open("t", idb);
    expect(b.toUrl("/p/thumb.png")).toMatch(/^blob:/);
    expect(b.toUrl("/p/none.png")).toBe("");
    b.close();
  });

  it("別々のデータベースは混ざらない", async () => {
    const idb = fresh();
    const a = await PersistentFs.open("one", idb);
    await a.writeTextAtomic("/x", "1");
    const b = await PersistentFs.open("two", idb);
    expect(await b.exists("/x")).toBe(false);
    a.close();
    b.close();
  });

  it("容量超過は SAVE_FAILED（保存できなかったことを伝える）", async () => {
    const idb = fresh();
    const a = await PersistentFs.open("t", idb);
    // put が QuotaExceededError になる状況を作る
    const orig = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function () {
      throw Object.assign(new Error("full"), { name: "QuotaExceededError" });
    };
    try {
      const e = await a.writeBytes("/p/big", bytes(10)).catch((x: unknown) => x);
      expect(e).toBeInstanceOf(AppError);
      expect(e).toMatchObject({ code: "SAVE_FAILED" });
      expect((e as AppError).message).toContain("容量");
    } finally {
      IDBObjectStore.prototype.put = orig;
    }
    // 失敗の後でも、次の書き込みは通る
    await a.writeBytes("/p/ok", bytes(3));
    a.close();
    const b = await PersistentFs.open("t", idb);
    expect(await b.exists("/p/ok")).toBe(true);
    b.close();
  });
});

describe("RoutingFs", () => {
  async function setup() {
    const idb = fresh();
    const persistent = await PersistentFs.open("t", idb);
    const volatile = new MemoryFs();
    const fs = new RoutingFs(persistent, volatile, (p) => p.startsWith("/mem/drops/"));
    return { fs, persistent, volatile, idb };
  }

  it("ドロップしたファイルは volatile に、それ以外は永続側に入り、開き直すと volatile だけ消える", async () => {
    const { fs, persistent, volatile, idb } = await setup();
    const dropped = volatile.putFile(new File([bytes(50)], "clip.mp4"));
    expect(dropped.startsWith("/mem/drops/")).toBe(true);
    await fs.writeTextAtomic("/mem/Movies/vvlog/p.vvlog/project.json", "{}");
    expect((await fs.stat(dropped)).size).toBe(50);
    expect(await persistent.exists(dropped)).toBe(false);
    persistent.close();
    const reopened = await PersistentFs.open("t", idb);
    expect(await reopened.exists("/mem/Movies/vvlog/p.vvlog/project.json")).toBe(true);
    expect(await reopened.exists(dropped)).toBe(false);
    reopened.close();
  });

  it("置き場をまたぐ copy は中身を運ぶ", async () => {
    const { fs, volatile, persistent } = await setup();
    const dropped = volatile.putFile(new File([bytes(20, 5)], "v.mp4"));
    await fs.copyFile(dropped, "/mem/Movies/vvlog/p.vvlog/source.mp4");
    expect(new Uint8Array(await persistent.readBytes("/mem/Movies/vvlog/p.vvlog/source.mp4"))).toEqual(bytes(20, 5));
  });

  it("volatile にある zip を、永続側へ展開できる（一時ファイルは残さない）", async () => {
    const { fs, volatile, persistent } = await setup();
    const zip = writeStoreZip([{ name: "p.vvlog/project.json", bytes: new TextEncoder().encode("{}") }]);
    const dropped = volatile.putFile(new File([zip], "p.zip"));
    await fs.unzip(dropped, "/mem/Movies/vvlog/imported");
    expect(await persistent.readText("/mem/Movies/vvlog/imported/p.vvlog/project.json")).toBe("{}");
    expect(await persistent.listDir("/mem/Movies/vvlog/imported")).toEqual(["p.vvlog"]);
  });

  it("追記・rename は持たない（fetchTransport がメモリに溜めて書く）", async () => {
    const { fs } = await setup();
    expect((fs as { appendBytes?: unknown }).appendBytes).toBeUndefined();
    expect((fs as { rename?: unknown }).rename).toBeUndefined();
  });
});
