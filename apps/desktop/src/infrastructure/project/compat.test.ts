// プロジェクトの互換性（#52、ADR-0007）：Tauri 版が作ったフォルダ形式の project を、新しい版（ブラウザ／ローカルサーバー）で開ける。
// 形式は変えない。フォルダ ⇄ zip の往復で欠けない。絶対パスの参照は書き換えず、元の動画が無ければ選び直す

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { OpenProject, SaveProject } from "@/application/usecases/project";
import { RelinkSource } from "@/application/usecases/media";
import type { Project } from "@/domain/project";
import { migrate, PROJECT_SCHEMA_VERSION } from "@/domain/project";
import { MemoryFs } from "../fs/memoryFs";
import { readZip } from "../fs/storeZip";
import { FsProjectArchive } from "./FsProjectArchive";
import { FsProjectRepository } from "./FsProjectRepository";

const FIXTURE = fileURLToPath(new URL("../../../../../fixtures/synth/synth.vvlog", import.meta.url));
const P = "/mem/Movies/vvlog/synth.vvlog";

function walk(dir: string, rel = ""): string[] {
  return readdirSync(join(dir, rel)).flatMap((n) => {
    const r = rel ? `${rel}/${n}` : n;
    return statSync(join(dir, r)).isDirectory() ? walk(dir, r) : [r];
  });
}

/** ディスク上のプロジェクトフォルダを、そのまま MemoryFs の dest に載せる（Tauri 版が書いたバイト列そのもの） */
async function load(fs: MemoryFs, dest = P) {
  for (const f of walk(FIXTURE)) await fs.writeBytes(`${dest}/${f}`, new Uint8Array(readFileSync(join(FIXTURE, f))));
}

function repo(fs: MemoryFs) {
  return new FsProjectRepository(fs, {
    projectsDir: async () => "/mem/Movies/vvlog",
    appDataDir: async () => "/mem/AppData",
  });
}

describe("Tauri 版が作ったプロジェクトを新しい版で開く", () => {
  it("フィクスチャ（解析・描画済み）が開け、内容がそのまま読める", async () => {
    const fs = new MemoryFs();
    await load(fs);
    const projects = repo(fs);
    const { project, overrides } = await new OpenProject({ projects }).execute(P);
    expect(project.schemaVersion).toBe(PROJECT_SCHEMA_VERSION);
    expect(project.name).toBe("合成フィクスチャ v0");
    expect(project.persons.map((p) => p.personId)).toEqual([1, 2]);
    expect(project.cast).toHaveLength(2);
    expect(project.jobs.analyze?.status).toBe("succeeded");
    expect(project.jobs.render?.castHash).toBe("02fb7031513986");
    expect(project.meta?.frameCount).toBe(96);
    expect(overrides.items).toBeDefined();
    // キャッシュの成果物（サムネ・レイヤー）が、プロジェクト相対のパスで解決できる
    expect(await fs.exists(projects.resolve(project.id, project.persons[0]!.thumb!))).toBe(true);
    expect(await fs.exists(`${P}/cache/job_synth_render/layers.json`)).toBe(true);
  });

  it("開いて保存し直しても、project.json の内容（更新日時以外）は変わらない", async () => {
    const fs = new MemoryFs();
    await load(fs);
    const projects = repo(fs);
    const before = JSON.parse(await fs.readText(`${P}/project.json`));
    const { project } = await new OpenProject({ projects }).execute(P);
    await new SaveProject({
      projects,
      clock: { now: () => new Date("2027-01-01T00:00:00Z"), sleep: async () => {} },
    }).execute(project);
    const after = JSON.parse(await fs.readText(`${P}/project.json`));
    expect({ ...after, updatedAt: before.updatedAt }).toEqual(before);
    expect(after.updatedAt).toBe("2027-01-01T00:00:00.000Z");
  });

  it("新しすぎる版は PROJECT_TOO_NEW、壊れた JSON は PROJECT_INVALID、無い項目は補う", async () => {
    const raw = JSON.parse(readFileSync(join(FIXTURE, "project.json"), "utf-8"));
    expect(() => migrate({ ...raw, schemaVersion: PROJECT_SCHEMA_VERSION + 1 })).toThrowError(
      expect.objectContaining({ code: "PROJECT_TOO_NEW" }),
    );
    expect(() => migrate("nope")).toThrowError(expect.objectContaining({ code: "PROJECT_INVALID" }));
    expect(() => migrate({ ...raw, schemaVersion: -1 })).toThrowError(
      expect.objectContaining({ code: "PROJECT_INVALID" }),
    );
    const minimal = migrate({ schemaVersion: 1, id: "p", name: "x", source: { path: "a.mp4" } });
    expect(minimal.cast).toEqual([]);
    expect(minimal.jobs.exports).toEqual([]);
    expect(minimal.finish).toBeDefined();
  });
});

describe("絶対パスの参照", () => {
  const sources = [
    "/Users/me/Movies/clip.mp4",
    "/home/me/Videos/clip.mp4",
    "C:\\Users\\me\\Videos\\clip.mp4",
    "D:/clips/ダンス.mov",
    "\\\\nas\\share\\clip.mp4",
  ];

  it.each(sources)("source.path = %s は書き換えずにそのまま持ち、保存しても変わらない", async (path) => {
    const raw = JSON.parse(readFileSync(join(FIXTURE, "project.json"), "utf-8"));
    raw.source.path = path;
    raw.jobs.exports = [
      {
        jobId: "j1",
        status: "succeeded",
        overridesHash: "h",
        file: "C:\\Users\\me\\Movies\\out.mp4",
        finishedAt: "2026-10-24T00:00:00Z",
      },
      { jobId: "j2", status: "succeeded", overridesHash: "h", file: "exports/out2.mp4" },
    ];
    const fs = new MemoryFs();
    await fs.writeTextAtomic(`${P}/project.json`, JSON.stringify(raw));
    const projects = repo(fs);
    const { project } = await new OpenProject({ projects }).execute(P);
    expect(project.source.path).toBe(path);
    expect(project.jobs.exports.map((e) => e.file)).toEqual(["C:\\Users\\me\\Movies\\out.mp4", "exports/out2.mp4"]);
    await new SaveProject({ projects, clock: { now: () => new Date(), sleep: async () => {} } }).execute(project);
    expect(JSON.parse(await fs.readText(`${P}/project.json`)).source.path).toBe(path);
  });
});

describe("元の動画が見つからないとき（RelinkSource）", () => {
  const project = (sha256?: string): Project =>
    migrate({ schemaVersion: 1, id: "p", name: "x", source: { path: "/gone/clip.mp4", sha256, durationSec: 3 } });
  const deps = (hash: string, exists = true) => ({
    files: { exists: async () => exists } as never,
    hasher: { sha256: async (_p: string, on?: (r: number) => void) => (on?.(1), hash) },
  });

  it("同じ動画（sha256 一致）なら source.path だけを差し替える。大文字小文字の違いは無視", async () => {
    const apply = await new RelinkSource(deps("ABCDEF")).execute({ project: project("abcdef"), path: "/new/clip.mp4" });
    const next = apply(project("abcdef"));
    expect(next.source).toMatchObject({ path: "/new/clip.mp4", sha256: "abcdef", durationSec: 3 });
  });

  it("違う動画は VALIDATION_FAILED（解析と合わなくなるのを防ぐ）", async () => {
    await expect(
      new RelinkSource(deps("other")).execute({ project: project("abcdef"), path: "/new/b.mp4" }),
    ).rejects.toMatchObject({
      code: "VALIDATION_FAILED",
    });
  });

  it("sha256 がまだ無い（送信前）プロジェクトは、選んだ動画をそのまま結びつける（計算しない）", async () => {
    const calls: string[] = [];
    const d = {
      files: { exists: async () => true } as never,
      hasher: { sha256: async (p: string) => (calls.push(p), "x") },
    };
    const apply = await new RelinkSource(d).execute({ project: project(undefined), path: "/new/clip.mov" });
    expect(apply(project(undefined)).source.path).toBe("/new/clip.mov");
    expect(calls).toEqual([]);
  });

  it("動画ではない拡張子・開けないファイルは断る", async () => {
    await expect(
      new RelinkSource(deps("x")).execute({ project: project("x"), path: "/new/a.txt" }),
    ).rejects.toMatchObject({
      code: "VALIDATION_FAILED",
    });
    await expect(
      new RelinkSource(deps("x", false)).execute({ project: project("x"), path: "/new/a.mp4" }),
    ).rejects.toMatchObject({
      code: "NOT_FOUND",
    });
  });
});

describe("フォルダ ⇄ zip の往復", () => {
  it("書き出して別の保存領域に読み込むと、cache/ 以外のファイルがバイト単位で一致し、開ける", async () => {
    const a = new MemoryFs();
    await load(a);
    const zip = await new FsProjectArchive(a).pack(P);
    const b = new MemoryFs();
    await new FsProjectArchive(b).unpack(zip, "/mem/Movies/vvlog/restored.vvlog");

    const want = walk(FIXTURE).filter((f) => !f.startsWith("cache/"));
    expect(want.length).toBeGreaterThan(2);
    for (const f of want) {
      expect(new Uint8Array(await b.readBytes(`/mem/Movies/vvlog/restored.vvlog/${f}`)), f).toEqual(
        new Uint8Array(readFileSync(join(FIXTURE, f))),
      );
    }
    expect(await b.exists("/mem/Movies/vvlog/restored.vvlog/cache")).toBe(false);
    const { project } = await new OpenProject({ projects: repo(b) }).execute("/mem/Movies/vvlog/restored.vvlog");
    expect(project.name).toBe("合成フィクスチャ v0");
  });

  it("往復をもう一度しても同じ（zip の中身が安定している）", async () => {
    const a = new MemoryFs();
    await load(a);
    const archive = new FsProjectArchive(a);
    const z1 = await readZip(await archive.pack(P));
    const b = new MemoryFs();
    await new FsProjectArchive(b).unpack(await archive.pack(P), P);
    const z2 = await readZip(await new FsProjectArchive(b).pack(P));
    const norm = (es: { name: string; bytes: Uint8Array }[]) => es.map((e) => [e.name, [...e.bytes].join(",")]).sort();
    expect(norm(z2)).toEqual(norm(z1));
  });
});
