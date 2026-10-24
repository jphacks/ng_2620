// アプリ全体の UI 状態（現在のプロジェクト ID・ワークスペース・ジョブパネル）

import { create } from "zustand";
import type { Workspace } from "../commands/registry";

interface AppState {
  projectId: string | null;
  workspace: Workspace | null;
  jobDrawerOpen: boolean;
  setLocation(projectId: string | null, workspace: Workspace | null): void;
  setJobDrawerOpen(open: boolean): void;
  toggleJobDrawer(): void;
}

export const useAppStore = create<AppState>((set) => ({
  projectId: null,
  workspace: null,
  jobDrawerOpen: false,
  setLocation: (projectId, workspace) => set({ projectId, workspace }),
  setJobDrawerOpen: (jobDrawerOpen) => set({ jobDrawerOpen }),
  toggleJobDrawer: () => set((s) => ({ jobDrawerOpen: !s.jobDrawerOpen })),
}));
