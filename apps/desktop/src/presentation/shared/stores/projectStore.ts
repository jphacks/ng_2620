// 開いているプロジェクト（F01）。変更から 1 秒後に自動保存、⌘S で即時。サーバーとの照合（RestoreJobs）もここから始める

import { create } from "zustand";
import type { LocalMediaInfo } from "@/domain/media";
import { AppError, toAppError } from "@/domain/errors";
import type { Project, ProjectMutation } from "@/domain/project";
import { resetServerState } from "@/domain/project";
import { getServices } from "../services";
import { currentBackend, useConnectionStore } from "./connectionStore";
import { useJobsStore } from "./jobsStore";

export type SaveState = "saved" | "dirty" | "saving" | "error";
export type RestoreState = "idle" | "checking" | "none" | "ok" | "offline" | "server_missing";

export interface ProjectSession {
  project: Project;
  path: string;
  /** サンプルプロジェクト（サーバー無しで開く、ADR-0009） */
  offline: boolean;
}

interface ProjectStore {
  current: ProjectSession | null;
  saveState: SaveState;
  saveError: AppError | null;
  restore: RestoreState;
  /** 開いたプロジェクトが別の接続で解析されている（その profileId） */
  profileMismatch: string | null;
  openAt(path: string): Promise<Project>;
  createFrom(sourcePath: string, info: LocalMediaInfo): Promise<Project>;
  close(): Promise<void>;
  update(fn: ProjectMutation): void;
  saveNow(): Promise<void>;
  restoreJobs(): Promise<void>;
  resetServerData(): void;
  dismissMismatch(): void;
}

const AUTOSAVE_MS = 1000;
let timer: ReturnType<typeof setTimeout> | null = null;
let chain: Promise<void> = Promise.resolve();

export const useProjectStore = create<ProjectStore>((set, get) => {
  const doSave = async () => {
    const s = get().current;
    if (!s) return;
    const snapshot = s.project;
    set({ saveState: "saving" });
    try {
      const saved = await getServices().uc.saveProject.execute(snapshot);
      const cur = get().current;
      if (!cur || cur.project.id !== saved.id) return;
      if (cur.project === snapshot) set({ current: { ...cur, project: saved }, saveState: "saved", saveError: null });
      else set({ saveState: "dirty" }); // 保存中に変わった → 次の自動保存で
    } catch (e) {
      console.error("[save]", e);
      set({ saveState: "error", saveError: new AppError("SAVE_FAILED", toAppError(e).message) });
    }
  };

  const schedule = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void get().saveNow(), AUTOSAVE_MS);
  };

  return {
    current: null,
    saveState: "saved",
    saveError: null,
    restore: "idle",
    profileMismatch: null,

    async openAt(path) {
      const cur = get().current;
      if (cur && cur.path === path) return cur.project;
      await get().close();
      const { project } = await getServices().uc.openProject.execute(path);
      const conn = useConnectionStore.getState();
      const mismatch =
        project.compute.serverProjectId && project.compute.profileId && project.compute.profileId !== conn.activeProfileId ? project.compute.profileId : null;
      set({ current: { project, path, offline: !!project.sample }, saveState: "saved", saveError: null, restore: "idle", profileMismatch: mismatch });
      if (!mismatch) void get().restoreJobs();
      return project;
    },

    async createFrom(sourcePath, info) {
      await get().close();
      const { project, path } = await getServices().uc.createProject.execute({
        sourcePath,
        info,
        profileId: useConnectionStore.getState().activeProfileId,
      });
      set({ current: { project, path, offline: false }, saveState: "saved", saveError: null, restore: "none", profileMismatch: null });
      return project;
    },

    async close() {
      if (!get().current) return;
      if (get().saveState === "dirty" || timer) await get().saveNow();
      useJobsStore.getState().stopAll();
      set({ current: null, restore: "idle", profileMismatch: null, saveState: "saved" });
    },

    update(fn) {
      const cur = get().current;
      if (!cur) return;
      const next = fn(cur.project);
      if (next === cur.project) return;
      set({ current: { ...cur, project: next }, saveState: "dirty" });
      schedule();
    },

    saveNow() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      chain = chain.then(doSave, doSave);
      return chain;
    },

    async restoreJobs() {
      const s = get().current;
      if (!s || s.offline) {
        set({ restore: "none" });
        return;
      }
      set({ restore: "checking" });
      const res = await getServices().uc.restoreJobs.execute({ project: s.project, backend: currentBackend() });
      if (get().current?.project.id !== s.project.id) return;
      if (res.status !== "ok") {
        set({ restore: res.status });
        return;
      }
      get().update(res.apply);
      set({ restore: "ok" });
      for (const job of res.jobs) useJobsStore.getState().adopt(job);
    },

    resetServerData() {
      useJobsStore.getState().stopAll();
      get().update(resetServerState);
      set({ restore: "none" });
    },

    dismissMismatch() {
      set({ profileMismatch: null });
      void get().restoreJobs();
    },
  };
});

/** プロジェクト相対のパス → 絶対パス */
export function resolveInProject(rel: string): string | null {
  const s = useProjectStore.getState().current;
  if (!s) return null;
  return getServices().paths.resolve(s.project.id, rel);
}
