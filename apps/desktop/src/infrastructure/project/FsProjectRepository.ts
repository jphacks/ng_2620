// ProjectRepositoryPort のファイル版（ADR-0007）。Tauri では Rust の write_atomic / move_to_trash を使う（TauriProjectRepository）
//   <名前>.vvlog/ project.json, overrides.json, cache/<jobId>/, exports/
//   最近の一覧はアプリデータの recent.json

import type { ProjectRepositoryPort, RecentEntry } from "@/application/ports/project";
import { RECENT_LIMIT } from "@/application/ports/project";
import type { OverridesDoc } from "@/domain/overrides";
import { emptyOverrides, parseOverrides } from "@/domain/overrides";
import type { Project } from "@/domain/project";
import { PROJECT_EXT, uniqueName } from "@/domain/project";
import type { FileSystem } from "../fs/FileSystem";
import { joinPath } from "../fs/path";

export interface ProjectDirs {
  /** 既定の保存先（~/Movies/vvlog） */
  projectsDir(): Promise<string>;
  /** recent.json を置くアプリデータのフォルダ */
  appDataDir(): Promise<string>;
}

export class FsProjectRepository implements ProjectRepositoryPort {
  private readonly paths = new Map<string, string>();

  constructor(
    private readonly fs: FileSystem,
    private readonly dirs: ProjectDirs,
  ) {}

  defaultDir() {
    return this.dirs.projectsDir();
  }

  async allocate(name: string, dir?: string) {
    const base = dir ?? (await this.dirs.projectsDir());
    await this.fs.ensureDir(base);
    const taken = (await this.fs.listDir(base))
      .filter((n) => n.toLowerCase().endsWith(PROJECT_EXT))
      .map((n) => n.slice(0, -PROJECT_EXT.length));
    const unique = uniqueName(name, taken);
    return { name: unique, path: joinPath(base, `${unique}${PROJECT_EXT}`) };
  }

  async create(path: string, project: Project) {
    await this.fs.ensureDir(path);
    await this.fs.ensureDir(joinPath(path, "cache"));
    await this.fs.ensureDir(joinPath(path, "exports"));
    await this.fs.writeTextAtomic(joinPath(path, "project.json"), JSON.stringify(project, null, 2));
    await this.fs.writeTextAtomic(joinPath(path, "overrides.json"), JSON.stringify(emptyOverrides(), null, 2));
    this.paths.set(project.id, path);
  }

  async open(path: string, migrate: (raw: unknown) => Project) {
    const raw: unknown = JSON.parse(await this.fs.readText(joinPath(path, "project.json")));
    const project = migrate(raw);
    this.paths.set(project.id, path);
    return project;
  }

  private dirOf(projectId: string): string {
    const p = this.paths.get(projectId);
    if (!p) throw new Error(`プロジェクトの場所がわかりません: ${projectId}`);
    return p;
  }

  async save(project: Project) {
    await this.fs.writeTextAtomic(joinPath(this.dirOf(project.id), "project.json"), JSON.stringify(project, null, 2));
  }

  async saveOverrides(projectId: string, doc: OverridesDoc) {
    await this.fs.writeTextAtomic(joinPath(this.dirOf(projectId), "overrides.json"), JSON.stringify(doc, null, 2));
  }

  async loadOverrides(projectId: string) {
    const file = joinPath(this.dirOf(projectId), "overrides.json");
    if (!(await this.fs.exists(file))) return emptyOverrides();
    return parseOverrides(JSON.parse(await this.fs.readText(file)));
  }

  pathOf(projectId: string) {
    return this.paths.get(projectId) ?? null;
  }

  resolve(projectId: string, rel: string) {
    return joinPath(this.dirOf(projectId), rel);
  }

  cacheDir(project: Project, jobId: string) {
    return joinPath(this.dirOf(project.id), "cache", jobId);
  }

  exportsDir(project: Project) {
    return joinPath(this.dirOf(project.id), "exports");
  }

  private async recentFile() {
    const dir = await this.dirs.appDataDir();
    await this.fs.ensureDir(dir);
    return joinPath(dir, "recent.json");
  }

  async listRecent(): Promise<RecentEntry[]> {
    const file = await this.recentFile();
    if (!(await this.fs.exists(file))) return [];
    try {
      const raw = JSON.parse(await this.fs.readText(file)) as { items?: RecentEntry[] };
      return Array.isArray(raw.items) ? raw.items : [];
    } catch {
      return [];
    }
  }

  private async writeRecent(items: RecentEntry[]) {
    await this.fs.writeTextAtomic(
      await this.recentFile(),
      JSON.stringify({ schemaVersion: 1, items: items.slice(0, RECENT_LIMIT) }, null, 2),
    );
  }

  async touchRecent(entry: RecentEntry) {
    const items = await this.listRecent();
    await this.writeRecent([entry, ...items.filter((e) => e.path !== entry.path)]);
  }

  async forgetRecent(path: string) {
    await this.writeRecent((await this.listRecent()).filter((e) => e.path !== path));
  }

  async remove(path: string) {
    await this.fs.moveToTrash(path);
    for (const [id, p] of this.paths) if (p === path) this.paths.delete(id);
  }

  async clearCache(project: Project) {
    const dir = joinPath(this.dirOf(project.id), "cache");
    await this.fs.remove(dir);
    await this.fs.ensureDir(dir);
  }
}
