// ProjectRepositoryPort：プロジェクトフォルダ（*.vvlog/）の読み書き（F01、ADR-0007）

import type { OverridesDoc } from "@/domain/overrides";
import type { Project, ProjectPhase } from "@/domain/project";

export interface RecentEntry {
  path: string; // プロジェクトフォルダの絶対パス
  projectId: string;
  name: string;
  updatedAt: string;
  phase: ProjectPhase;
  overridesCount: number;
  thumb?: string; // 絶対パス
  isSample: boolean;
}

export const RECENT_LIMIT = 20;

export interface ProjectRepositoryPort {
  /** 既定の保存先（~/Movies/vvlog） */
  defaultDir(): Promise<string>;
  /** 重複しない名前と、そのフォルダのパスを決める（作りはしない） */
  allocate(name: string, dir?: string): Promise<{ name: string; path: string }>;
  /** フォルダを作り、project.json と overrides.json を書く */
  create(path: string, project: Project): Promise<void>;
  /** project.json を読み、migrate で現行版にして返す。以後 project.id でこの場所を覚える */
  open(path: string, migrate: (raw: unknown) => Project): Promise<Project>;
  /** 原子的に保存（一時ファイル → rename） */
  save(project: Project): Promise<void>;
  saveOverrides(projectId: string, doc: OverridesDoc): Promise<void>;
  loadOverrides(projectId: string): Promise<OverridesDoc>;
  /** 開いているプロジェクトのフォルダ（知らなければ null） */
  pathOf(projectId: string): string | null;
  /** プロジェクトフォルダ相対のパスを絶対パスに */
  resolve(projectId: string, rel: string): string;
  /** cache/<jobId>/ の絶対パス */
  cacheDir(project: Project, jobId: string): string;
  exportsDir(project: Project): string;

  listRecent(): Promise<RecentEntry[]>;
  touchRecent(entry: RecentEntry): Promise<void>;
  forgetRecent(path: string): Promise<void>;
  /** ゴミ箱へ移動 */
  remove(path: string): Promise<void>;
  /** cache/ を消す（再取得できる） */
  clearCache(project: Project): Promise<void>;
}
