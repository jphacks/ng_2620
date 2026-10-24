// ProjectRepositoryPort のメモリ版（テスト・レーン B のエディタ単独起動 /#/dev/editor?fixture=v0 用）。
// seed() でフィクスチャの project.json / overrides.json を入れてから open() する

import type { ProjectRepositoryPort, RecentEntry } from "@/application/ports/project";
import { RECENT_LIMIT } from "@/application/ports/project";
import type { OverridesDoc } from "@/domain/overrides";
import { emptyOverrides, parseOverrides } from "@/domain/overrides";
import type { Project } from "@/domain/project";
import { PROJECT_EXT, uniqueName } from "@/domain/project";

export class InMemoryProjectRepository implements ProjectRepositoryPort {
  private readonly raw = new Map<string, { project: unknown; overrides: OverridesDoc }>();
  private readonly paths = new Map<string, string>();
  private recent: RecentEntry[] = [];
  removed: string[] = [];

  constructor(private readonly root = "/memory/projects") {}

  /** フィクスチャの project.json（と overrides.json）を path に置く */
  seed(path: string, project: unknown, overrides?: unknown) {
    this.raw.set(path, { project, overrides: overrides ? parseOverrides(overrides) : emptyOverrides() });
  }

  async defaultDir() {
    return this.root;
  }

  async allocate(name: string, dir?: string) {
    const base = dir ?? this.root;
    const taken = [...this.raw.keys()].filter((p) => p.startsWith(`${base}/`)).map((p) => p.slice(base.length + 1, -PROJECT_EXT.length));
    const unique = uniqueName(name, taken);
    return { name: unique, path: `${base}/${unique}${PROJECT_EXT}` };
  }

  async create(path: string, project: Project) {
    this.raw.set(path, { project: structuredClone(project), overrides: emptyOverrides() });
    this.paths.set(project.id, path);
  }

  async open(path: string, migrate: (raw: unknown) => Project) {
    const hit = this.raw.get(path);
    if (!hit) throw new Error(`プロジェクトがありません: ${path}`);
    const project = migrate(structuredClone(hit.project));
    this.paths.set(project.id, path);
    return project;
  }

  private entry(projectId: string) {
    const path = this.paths.get(projectId);
    const hit = path ? this.raw.get(path) : undefined;
    if (!path || !hit) throw new Error(`プロジェクトの場所がわかりません: ${projectId}`);
    return { path, hit };
  }

  async save(project: Project) {
    const { path, hit } = this.entry(project.id);
    this.raw.set(path, { ...hit, project: structuredClone(project) });
  }

  async saveOverrides(projectId: string, doc: OverridesDoc) {
    const { path, hit } = this.entry(projectId);
    this.raw.set(path, { ...hit, overrides: structuredClone(doc) });
  }

  async loadOverrides(projectId: string) {
    return structuredClone(this.entry(projectId).hit.overrides);
  }

  pathOf(projectId: string) {
    return this.paths.get(projectId) ?? null;
  }

  resolve(projectId: string, rel: string) {
    return `${this.entry(projectId).path}/${rel}`;
  }

  cacheDir(project: Project, jobId: string) {
    return this.resolve(project.id, `cache/${jobId}`);
  }

  exportsDir(project: Project) {
    return this.resolve(project.id, "exports");
  }

  async listRecent() {
    return this.recent;
  }

  async touchRecent(entry: RecentEntry) {
    this.recent = [entry, ...this.recent.filter((e) => e.path !== entry.path)].slice(0, RECENT_LIMIT);
  }

  async forgetRecent(path: string) {
    this.recent = this.recent.filter((e) => e.path !== path);
  }

  async remove(path: string) {
    this.raw.delete(path);
    this.removed.push(path);
  }

  async clearCache() {}
}
