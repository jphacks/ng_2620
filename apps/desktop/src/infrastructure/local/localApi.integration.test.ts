// 実際のローカルアプリサーバー（services/gpu_server、VVLOG_LOCAL=1）を起動して、フルモードのアダプタを通しで確かめる。
// Python の環境 A（services/gpu_server/.venv、または VVLOG_TEST_PYTHON）が無ければ丸ごと skip する

import { spawn, type ChildProcess } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { AppError } from "@/domain/errors";
import { fetchTransport } from "../compute/fetchTransport";
import { HttpFileSystem } from "../fs/httpFs";
import { HttpDialog } from "../system/httpDialog";
import { sweepImports, uploadFileToServer } from "../system/serverDrop";
import { HttpFileHasher } from "../media/hashers";
import { fetchLocalInfo, LocalApi, type LocalInfo } from "./localApi";

const server = fileURLToPath(new URL("../../../../../services/gpu_server", import.meta.url));
const python =
  process.env.VVLOG_TEST_PYTHON ??
  [join(server, ".venv", "bin", "python"), join(server, ".venv", "Scripts", "python.exe")].find(existsSync);

describe.skipIf(!python)("ローカルアプリサーバー（実サーバー）", () => {
  let proc: ChildProcess;
  let tmp: string;
  let base: string;
  let info: LocalInfo;
  let api: LocalApi;
  let fs: HttpFileSystem;
  let projects: string;

  beforeAll(async () => {
    tmp = mkdtempSync(join(tmpdir(), "vvlog-it-"));
    projects = join(tmp, "projects");
    for (const d of ["dist", "projects", "appdata", "config"]) mkdirSync(join(tmp, d));
    writeFileSync(join(tmp, "dist", "index.html"), "<!doctype html><title>vvlog</title>");
    proc = spawn(python!, ["-m", "vvlog_server"], {
      cwd: server,
      env: {
        ...process.env,
        VVLOG_LOCAL: "1",
        VVLOG_PORT: "0",
        VVLOG_SPA_DIR: join(tmp, "dist"),
        VVLOG_DATA_ROOT: join(tmp, "data"),
        VVLOG_LOCAL_PROJECTS_DIR: projects,
        VVLOG_LOCAL_APPDATA_DIR: join(tmp, "appdata"),
        VVLOG_LOCAL_CONFIG_DIR: join(tmp, "config"),
      },
    });
    base = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("サーバーが起動しませんでした")), 30_000);
      let out = "";
      proc.stdout!.on("data", (b: Buffer) => {
        out += b.toString();
        const m = /VVLOG_LOCAL_READY (http:\/\/127\.0\.0\.1:\d+)\//.exec(out);
        if (m) {
          clearTimeout(timer);
          resolve(m[1]!);
        }
      });
      proc.on("exit", (code) => reject(new Error(`サーバーが終了しました（${code}）`)));
    });
    const got = await fetchLocalInfo({ base });
    if (!got) throw new Error("GET /v1/local/info に答えがありません");
    info = got;
    api = new LocalApi(info, base);
    fs = new HttpFileSystem(api);
  }, 40_000);

  afterAll(() => {
    proc?.kill();
    if (tmp) rmSync(tmp, { recursive: true, force: true });
  });

  it("フルモードの判定：info がトークンと許可フォルダを返す", () => {
    expect(info.mode).toBe("local");
    expect(info.spa).toBe(true);
    expect(info.token).toMatch(/^[0-9a-f]{48}$/);
    expect(info.dirs.projects.toLowerCase()).toBe(projects.toLowerCase());
  });

  it("サーバーが居なければ null（Pages など）", async () => {
    expect(await fetchLocalInfo({ base: "http://127.0.0.1:9", timeoutMs: 800 })).toBeNull();
  });

  it("画面を配信し、別 Origin にはトークンを渡さない", async () => {
    expect(await (await fetch(`${base}/`)).text()).toContain("<title>vvlog</title>");
    const cross = await fetch(`${base}/v1/local/info`, { headers: { "Sec-Fetch-Site": "cross-site" } });
    expect(await cross.json()).not.toHaveProperty("token");
    const evil = await fetch(`${base}/v1/local/info`, { headers: { Origin: "https://evil.example" } });
    expect(evil.status).toBe(403);
  });

  it("テキスト：原子的に書いて読む（日本語・親フォルダの自動作成・private）", async () => {
    const f = join(projects, "動画", "a", "project.json");
    await fs.writeTextAtomic(f, '{"名前":"テスト"}');
    expect(await fs.readText(f)).toBe('{"名前":"テスト"}');
    await fs.writeTextAtomic(f, "v2", { private: true });
    expect(await fs.readText(f)).toBe("v2");
    expect(await fs.listDir(join(projects, "動画", "a"))).toEqual(["project.json"]);
  });

  it("バイト列：範囲読み・末尾の外は空・全体・追記・rename", async () => {
    const f = join(projects, "bin", "v.bin");
    const data = randomBytes(10_000);
    await fs.writeBytes(f, data);
    expect(await fs.readBytes(f)).toEqual(new Uint8Array(data));
    expect(await fs.readChunk(f, 100, 50)).toEqual(new Uint8Array(data.subarray(100, 150)));
    expect(await fs.readChunk(f, 9_990, 100)).toEqual(new Uint8Array(data.subarray(9_990)));
    expect(await fs.readChunk(f, 20_000, 10)).toEqual(new Uint8Array(0));
    expect((await fs.stat(f)).size).toBe(10_000);

    const part = join(projects, "bin", "d.zip.part");
    expect(await fs.appendBytes(part, new Uint8Array([1, 2, 3]), 0)).toBe(3);
    expect(await fs.appendBytes(part, new Uint8Array([4, 5]), 3)).toBe(5);
    await expect(fs.appendBytes(part, new Uint8Array([9]), 3)).rejects.toMatchObject({ code: "FS_CONFLICT" });
    await fs.rename(part, join(projects, "bin", "d.zip"));
    expect(await fs.exists(part)).toBe(false);
    expect(await fs.readBytes(join(projects, "bin", "d.zip"))).toEqual(new Uint8Array([1, 2, 3, 4, 5]));
  });

  it("コピー・フォルダ・削除、無いものは NOT_FOUND", async () => {
    const a = join(projects, "c", "a.txt");
    await fs.writeTextAtomic(a, "hi");
    await fs.copyFile(a, join(projects, "c", "sub", "b.txt"));
    await fs.ensureDir(join(projects, "c", "empty"));
    expect((await fs.listDir(join(projects, "c"))).sort()).toEqual(["a.txt", "empty", "sub"]);
    await fs.remove(join(projects, "c"));
    expect(await fs.exists(a)).toBe(false);
    await expect(fs.stat(join(projects, "nope"))).rejects.toMatchObject({ code: "NOT_FOUND" });
    await fs.remove(join(projects, "never")); // 無くても何もしない
  });

  it("許可フォルダの外は PATH_NOT_ALLOWED（AppError）", async () => {
    const out = join(tmp, "outside.txt");
    writeFileSync(out, "secret");
    const e = await fs.readText(out).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(AppError);
    expect(e).toMatchObject({ code: "PATH_NOT_ALLOWED", status: 403 });
    await expect(fs.writeTextAtomic(out, "x")).rejects.toMatchObject({ code: "PATH_NOT_ALLOWED" });
    await expect(fs.readText(join(projects, "..", "outside.txt"))).rejects.toMatchObject({ code: "PATH_NOT_ALLOWED" });
  });

  it("toUrl：<img>・<video> がヘッダー無しで読める URL（Range も効く）", async () => {
    const f = join(projects, "u", "v.bin");
    await fs.writeBytes(f, new Uint8Array([10, 20, 30, 40, 50]));
    const res = await fetch(fs.toUrl(f), { headers: { Range: "bytes=1-3" } });
    expect(res.status).toBe(206);
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(new Uint8Array([20, 30, 40]));
    expect((await fetch(fs.toUrl(f).replace(/t=[0-9a-f]+/, "t=wrong"))).status).toBe(401);
  });

  it("fetchTransport：実サーバーから Range 再開でダウンロード（.part の続きから）", async () => {
    const src = join(projects, "dl", "big.bin");
    const data = randomBytes(3 * 1024 * 1024 + 123);
    await fs.writeBytes(src, data);
    const t = fetchTransport(fs, { fetchImpl: globalThis.fetch });
    const url = fs.toUrl(src);

    const full = join(projects, "dl", "full.bin");
    const seen: number[] = [];
    await t.download(url, {}, full, (r) => seen.push(r));
    expect(Buffer.from(await fs.readBytes(full)).equals(data)).toBe(true);
    expect(await fs.exists(`${full}.part`)).toBe(false);
    expect(seen.at(-1)).toBe(data.length);

    const resumed = join(projects, "dl", "resumed.bin");
    await fs.appendBytes(`${resumed}.part`, new Uint8Array(data.subarray(0, 1_000_000)));
    await t.download(url, {}, resumed);
    expect(Buffer.from(await fs.readBytes(resumed)).equals(data)).toBe(true);
  });

  it("HttpFileHasher：sha256 を進捗つきで（SSE）／進捗なしで", async () => {
    const f = join(projects, "h", "big.bin");
    const data = randomBytes(12 * 1024 * 1024);
    await fs.writeBytes(f, data);
    const want = createHash("sha256").update(data).digest("hex");
    const hasher = new HttpFileHasher(api);
    const ratios: number[] = [];
    expect(await hasher.sha256(f, (r) => ratios.push(r))).toBe(want);
    expect(ratios.length).toBeGreaterThan(2);
    expect(ratios).toEqual([...ratios].sort((a, b) => a - b));
    expect(await hasher.sha256(f)).toBe(want);
    await expect(hasher.sha256(join(tmp, "no.mp4"))).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("ドロップ取り込み：File を分割してサーバーの取り込みフォルダへ（中身が一致・.part が残らない・掃除）", async () => {
    const data = randomBytes(20 * 1024 * 1024 + 17);
    const dest = join(tmp, "appdata", "imports", "g1", "動画 clip.mp4");
    const seen: number[] = [];
    await uploadFileToServer(new Blob([data]), dest, fs as Required<HttpFileSystem>, {
      onProgress: (s) => seen.push(s),
    });
    expect(Buffer.from(await fs.readBytes(dest)).equals(data)).toBe(true);
    expect(seen).toEqual([8 * 1024 * 1024, 16 * 1024 * 1024, data.length]); // 8MiB ずつ
    expect(await fs.listDir(join(tmp, "appdata", "imports", "g1"))).toEqual(["動画 clip.mp4"]);
    // 中止：サーバーに .part も dest も残らない
    const ctl = new AbortController();
    await expect(
      uploadFileToServer(
        new Blob([data]),
        join(tmp, "appdata", "imports", "g2", "x.mp4"),
        fs as Required<HttpFileSystem>,
        {
          signal: ctl.signal,
          onProgress: () => ctl.abort(),
        },
      ),
    ).rejects.toThrow();
    expect(await fs.listDir(join(tmp, "appdata", "imports", "g2"))).toEqual([]);
    // 掃除：参照されていない取り込みだけが消える
    const importsDir = join(tmp, "appdata", "imports");
    await uploadFileToServer(
      new Blob([data.subarray(0, 10)]),
      join(importsDir, "g3", "keep.mp4"),
      fs as Required<HttpFileSystem>,
    );
    const removed = await sweepImports(
      fs,
      importsDir,
      [join(importsDir, "g3", "keep.mp4")],
      Date.now() + 3 * 86_400_000,
    );
    expect(removed).toBe(2); // g1 と、空になった g2
    expect(await fs.exists(join(importsDir, "g3", "keep.mp4"))).toBe(true);
  });

  it("ネイティブダイアログ：Windows 以外では DIALOG_UNAVAILABLE、別 Origin は DIALOG_LOCAL_ONLY", async () => {
    if (process.platform !== "win32") {
      await expect(new HttpDialog(api).pickFiles({})).rejects.toMatchObject({
        code: "DIALOG_UNAVAILABLE",
        status: 501,
      });
    }
    const res = await fetch(`${base}/v1/local/dialog/open-files`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${info.token}`,
        "Content-Type": "application/json",
        "Sec-Fetch-Site": "cross-site",
      },
      body: "{}",
    });
    expect(res.status).toBe(403);
    expect((await res.json()).error.code).toBe("DIALOG_LOCAL_ONLY");
  });

  it("media/probe：ffprobe があれば動画の情報、無ければ 503（画面は <video> で代用）", async () => {
    const mp4 = join(tmp, "clip.mp4");
    const ff = spawn("ffmpeg", [
      "-v",
      "error",
      "-y",
      "-f",
      "lavfi",
      "-i",
      "testsrc2=size=320x240:rate=30:duration=1",
      "-c:v",
      "libx264",
      "-pix_fmt",
      "yuv420p",
      mp4,
    ]);
    const made = await new Promise<boolean>((r) => {
      ff.on("error", () => r(false));
      ff.on("exit", (c) => r(c === 0));
    });
    if (!made) return; // ffmpeg が無い環境
    const r = await api.request("POST", "/media/probe", { json: { path: mp4 } }).then(
      (x) => x.json(),
      (e: unknown) => e,
    );
    if (r instanceof AppError) return expect(r.code).toBe("FFPROBE_UNAVAILABLE");
    expect(r).toMatchObject({ width: 320, height: 240, rotation: 0, codec: "h264", hasAudio: false });
  });
});
