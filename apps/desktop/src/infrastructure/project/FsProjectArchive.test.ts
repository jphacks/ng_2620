import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it, vi } from "vitest";
import type { FileExchangePort } from "@/application/ports/archive";
import type { ProjectRepositoryPort } from "@/application/ports/project";
import { ExportProjectZip, ImportProjectZip } from "@/application/usecases/archive";
import { MemoryFs } from "../fs/memoryFs";
import { PersistentFs } from "../fs/persistentFs";
import { readZip, writeStoreZip } from "../fs/storeZip";
import { FsProjectArchive } from "./FsProjectArchive";

const enc = (s: string) => new TextEncoder().encode(s);
const dec = (b: Uint8Array) => new TextDecoder().decode(b);
const P = "/mem/Movies/vvlog/demo.vvlog";

async function sampleProject(fs: MemoryFs) {
  await fs.writeTextAtomic(`${P}/project.json`, JSON.stringify({ schemaVersion: 1, name: "デモ" }));
  await fs.writeTextAtomic(`${P}/overrides.json`, '{"items":[]}');
  await fs.writeBytes(`${P}/thumbs/p1.png`, new Uint8Array([1, 2, 3]));
  await fs.writeBytes(`${P}/exports/out.mp4`, new Uint8Array(5000).fill(7));
  await fs.writeBytes(`${P}/cache/job1/layers/a.webp`, new Uint8Array([9, 9]));
  await fs.writeBytes(`${P}/cache/job1/.fetched/x.json`, new Uint8Array([1]));
  await fs.writeBytes(`${P}/empty.txt`, new Uint8Array(0));
  await fs.ensureDir(`${P}/emptydir`);
}

describe("FsProjectArchive.pack", () => {
  it("project.json・overrides・サムネ・書き出しを入れ、cache/ と空のフォルダは入れない。先頭は <名前>.vvlog/", async () => {
    const fs = new MemoryFs();
    await sampleProject(fs);
    const entries = await readZip(await new FsProjectArchive(fs).pack(P));
    const names = entries.map((e) => e.name).sort();
    expect(names).toEqual([
      "demo.vvlog/empty.txt",
      "demo.vvlog/exports/out.mp4",
      "demo.vvlog/overrides.json",
      "demo.vvlog/project.json",
      "demo.vvlog/thumbs/p1.png",
    ]);
    expect(dec(entries.find((e) => e.name.endsWith("project.json"))!.bytes)).toContain("デモ");
    expect(entries.find((e) => e.name.endsWith("out.mp4"))!.bytes.length).toBe(5000);
  });

  it("書きかけの .part は入れない。project.json が無ければ PROJECT_INVALID", async () => {
    const fs = new MemoryFs();
    await sampleProject(fs);
    await fs.writeBytes(`${P}/.layers.zip.part`, new Uint8Array([1]));
    const names = (await readZip(await new FsProjectArchive(fs).pack(P))).map((e) => e.name);
    expect(names.some((n) => n.endsWith(".part"))).toBe(false);
    await fs.writeBytes("/mem/Movies/vvlog/none.vvlog/readme.txt", new Uint8Array([1]));
    await expect(new FsProjectArchive(fs).pack("/mem/Movies/vvlog/none.vvlog")).rejects.toMatchObject({
      code: "PROJECT_INVALID",
    });
  });

  it("末尾のスラッシュ付きのパスでも同じ", async () => {
    const fs = new MemoryFs();
    await sampleProject(fs);
    const names = (await readZip(await new FsProjectArchive(fs).pack(`${P}/`))).map((e) => e.name);
    expect(names.every((n) => n.startsWith("demo.vvlog/"))).toBe(true);
  });
});

describe("FsProjectArchive.unpack", () => {
  it("書き出した zip を別の場所に展開すると、同じ中身（cache 以外）になる", async () => {
    const a = new MemoryFs();
    await sampleProject(a);
    const zip = await new FsProjectArchive(a).pack(P);
    const b = new MemoryFs();
    await new FsProjectArchive(b).unpack(zip, "/mem/Movies/vvlog/restored.vvlog");
    const R = "/mem/Movies/vvlog/restored.vvlog";
    expect(await b.readText(`${R}/project.json`)).toBe(await a.readText(`${P}/project.json`));
    expect(new Uint8Array(await b.readBytes(`${R}/exports/out.mp4`))).toEqual(new Uint8Array(5000).fill(7));
    expect(await b.exists(`${R}/cache`)).toBe(false);
    expect((await b.listDir(R)).sort()).toEqual(["empty.txt", "exports", "overrides.json", "project.json", "thumbs"]);
  });

  it("先頭のフォルダが無い zip（project.json が根）も読める", async () => {
    const fs = new MemoryFs();
    const zip = writeStoreZip([
      { name: "project.json", bytes: enc("{}") },
      { name: "thumbs/a.png", bytes: new Uint8Array([1]) },
    ]);
    await new FsProjectArchive(fs).unpack(zip, "/p/x.vvlog");
    expect(await fs.readText("/p/x.vvlog/project.json")).toBe("{}");
    expect(await fs.exists("/p/x.vvlog/thumbs/a.png")).toBe(true);
  });

  it("zip の外側のファイル（先頭フォルダの外）は取り込まない", async () => {
    const fs = new MemoryFs();
    const zip = writeStoreZip([
      { name: "demo.vvlog/project.json", bytes: enc("{}") },
      { name: "__MACOSX/demo.vvlog/._project.json", bytes: enc("x") },
      { name: "readme.txt", bytes: enc("x") },
    ]);
    await new FsProjectArchive(fs).unpack(zip, "/p/x.vvlog");
    expect(await fs.listDir("/p/x.vvlog")).toEqual(["project.json"]);
  });

  it.each(["../escape.txt", "demo.vvlog/../../escape.txt", "/abs.txt", "C:/win.txt", "demo.vvlog\\..\\..\\up.txt"])(
    "展開先の外を指す項目（%s）が1つでもあれば、何も書かずに断る",
    async (bad) => {
      const fs = new MemoryFs();
      const zip = writeStoreZip([
        { name: "demo.vvlog/project.json", bytes: enc("{}") },
        { name: bad, bytes: enc("pwned") },
      ]);
      await expect(new FsProjectArchive(fs).unpack(zip, "/p/x.vvlog")).rejects.toMatchObject({
        code: "PROJECT_INVALID",
      });
      expect(await fs.exists("/p/x.vvlog")).toBe(false);
      expect(await fs.exists("/escape.txt")).toBe(false);
    },
  );

  it("vvlog の zip ではない／壊れている zip は PROJECT_INVALID", async () => {
    const fs = new MemoryFs();
    const a = new FsProjectArchive(fs);
    await expect(a.unpack(writeStoreZip([{ name: "photo.jpg", bytes: enc("x") }]), "/p/x")).rejects.toMatchObject({
      code: "PROJECT_INVALID",
    });
    await expect(a.unpack(enc("this is not a zip at all, just text"), "/p/x")).rejects.toMatchObject({
      code: "PROJECT_INVALID",
    });
    await expect(
      a.unpack(writeStoreZip([{ name: "project.json", bytes: enc("{not json") }]), "/p/x"),
    ).rejects.toMatchObject({
      code: "PROJECT_INVALID",
    });
    expect(await fs.exists("/p/x")).toBe(false);
  });
});

describe("zip のユースケース", () => {
  function fakeExchange(pick: { name: string; bytes: Uint8Array } | null) {
    const downloads: { filename: string; bytes: Uint8Array; mime?: string }[] = [];
    const ex: FileExchangePort = {
      async download(filename, bytes, mime) {
        downloads.push({ filename, bytes, mime });
      },
      pickFile: vi.fn(async () => pick),
    };
    return { ex, downloads };
  }

  it("書き出し：<名前>.vvlog.zip としてダウンロードさせる（名前の記号は _ に）", async () => {
    const fs = new MemoryFs();
    await sampleProject(fs);
    const { ex, downloads } = fakeExchange(null);
    const name = await new ExportProjectZip({ archive: new FsProjectArchive(fs), exchange: ex }).execute({
      path: P,
      name: 'デモ: a/b?"',
    });
    expect(name).toBe("デモ_ a_b__.vvlog.zip");
    expect(downloads).toHaveLength(1);
    expect(downloads[0]!.mime).toBe("application/zip");
    expect((await readZip(downloads[0]!.bytes)).length).toBe(5);
  });

  it("読み込み：選ばれた zip を新しい名前のフォルダに展開し、パスを返す。選ばなければ null", async () => {
    const src = new MemoryFs();
    await sampleProject(src);
    const zip = await new FsProjectArchive(src).pack(P);
    const fs = new MemoryFs();
    const removed: string[] = [];
    const projects = {
      defaultDir: async () => "/mem/Movies/vvlog",
      allocate: async (name: string, dir?: string) => ({ name, path: `${dir}/${name}.vvlog` }),
      remove: async (p: string) => void removed.push(p),
    } as unknown as ProjectRepositoryPort;
    const archive = new FsProjectArchive(fs);

    const ok = await new ImportProjectZip({
      archive,
      exchange: fakeExchange({ name: "デモ.vvlog.zip", bytes: zip }).ex,
      projects,
    }).execute();
    expect(ok).toEqual({ path: "/mem/Movies/vvlog/デモ.vvlog" });
    expect(await fs.exists("/mem/Movies/vvlog/デモ.vvlog/project.json")).toBe(true);

    const none = await new ImportProjectZip({ archive, exchange: fakeExchange(null).ex, projects }).execute();
    expect(none).toBeNull();

    const bad = new ImportProjectZip({
      archive,
      exchange: fakeExchange({ name: "x.zip", bytes: enc("nope") }).ex,
      projects,
    });
    await expect(bad.execute()).rejects.toMatchObject({ code: "PROJECT_INVALID" });
    expect(removed).toEqual(["/mem/Movies/vvlog/x.vvlog"]); // 失敗したら作りかけのフォルダを片付ける
  });
});

describe("IndexedDB に保存したプロジェクトの zip 往復（Safari で消えても zip から戻せる）", () => {
  it("保存 → 閉じて開き直し → zip に書き出し → 空の保存領域に読み込むと、同じプロジェクトが開ける", async () => {
    const idb = new IDBFactory();
    const a = await PersistentFs.open("a", idb);
    await sampleProject(a);
    a.close();
    const reopened = await PersistentFs.open("a", idb);
    const zip = await new FsProjectArchive(reopened).pack(P); // 開き直した保存領域から
    // ブラウザがデータを消した状態（別の空のデータベース）に戻す
    const wiped = await PersistentFs.open("wiped", idb);
    expect(await wiped.exists(P)).toBe(false);
    await new FsProjectArchive(wiped).unpack(zip, P);
    expect(await wiped.readText(`${P}/project.json`)).toContain("デモ");
    expect((await wiped.stat(`${P}/exports/out.mp4`)).size).toBe(5000);
    reopened.close();
    wiped.close();
  });
});
