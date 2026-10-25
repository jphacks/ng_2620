// ProjectArchivePort の実装（FileSystem 越し）。zip は無圧縮（storeZip）。cache/ は入れない

import type { ProjectArchivePort } from "@/application/ports/archive";
import { AppError } from "@/domain/errors";
import type { FileSystem } from "../fs/FileSystem";
import { basenameOf, joinPath } from "../fs/path";
import { readZip, writeStoreZip, type ZipEntry } from "../fs/storeZip";

const SKIP_TOP = new Set(["cache"]);

const invalid = (message: string, detail?: Record<string, unknown>) =>
  new AppError("PROJECT_INVALID", message, { detail });

export class FsProjectArchive implements ProjectArchivePort {
  constructor(private readonly fs: FileSystem) {}

  async pack(projectPath: string): Promise<Uint8Array> {
    const top = basenameOf(projectPath.replace(/[\\/]+$/, "")) || "project.vvlog";
    const entries: ZipEntry[] = [];
    const walk = async (dir: string, rel: string) => {
      for (const name of await this.fs.listDir(dir)) {
        if (!rel && SKIP_TOP.has(name)) continue;
        if (name.startsWith(".") && name.endsWith(".part")) continue;
        const abs = joinPath(dir, name);
        const relName = rel ? `${rel}/${name}` : name;
        // フォルダなら中身がある（空のフォルダは zip に入れない）。ファイルは listDir が空か失敗する
        const children = await this.fs.listDir(abs).catch(() => []);
        if (children.length > 0) await walk(abs, relName);
        else if (await this.fs.exists(abs).catch(() => false)) {
          try {
            entries.push({ name: `${top}/${relName}`, bytes: await this.fs.readBytes(abs) });
          } catch {
            // 空のフォルダ
          }
        }
      }
    };
    await walk(projectPath, "");
    if (!entries.some((e) => e.name === `${top}/project.json`)) throw invalid("project.json がありません");
    return writeStoreZip(entries);
  }

  async unpack(zip: Uint8Array, destPath: string): Promise<void> {
    let entries: ZipEntry[];
    try {
      entries = await readZip(zip);
    } catch (e) {
      throw invalid(`zip を読めません（${e instanceof Error ? e.message : String(e)}）`);
    }
    for (const e of entries) {
      const parts = e.name.replace(/\\/g, "/").split("/");
      if (e.name.startsWith("/") || /^[a-z]:/i.test(e.name) || parts.includes("..")) {
        throw invalid("zip に、展開先の外を指す項目があります", { entry: e.name });
      }
    }
    // project.json の置き場所で、zip の先頭のフォルダ（<名前>.vvlog/）の有無を決める
    const manifest = entries
      .filter((e) => /(^|\/)project\.json$/.test(e.name))
      .sort((a, b) => a.name.length - b.name.length)[0];
    if (!manifest) throw invalid("project.json が入っていません。vvlog の zip ではないようです");
    const prefix = manifest.name.slice(0, manifest.name.length - "project.json".length);
    try {
      JSON.parse(new TextDecoder().decode(manifest.bytes));
    } catch {
      throw invalid("project.json が壊れています");
    }
    await this.fs.ensureDir(destPath);
    for (const e of entries) {
      if (!e.name.startsWith(prefix)) continue;
      const rel = e.name.slice(prefix.length);
      if (!rel) continue;
      await this.fs.writeBytes(joinPath(destPath, rel), e.bytes);
    }
  }
}
