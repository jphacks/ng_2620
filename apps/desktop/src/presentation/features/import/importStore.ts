// 取り込みの実行状態（アップロード中の進捗）。画面を離れても続く

import { create } from "zustand";
import type { ImportProgress } from "@/application/usecases";
import type { AppError } from "@/domain/errors";
import { toAppError } from "@/domain/errors";
import { getServices } from "@/presentation/shared/services";
import { currentBackend } from "@/presentation/shared/stores/connectionStore";
import { useJobsStore } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";

interface ImportRun {
  status: "running" | "done" | "error";
  progress: ImportProgress | null;
  error: AppError | null;
}

interface ImportStore {
  runs: Record<string, ImportRun>;
  /** サーバー側プロジェクト作成 → アップロード（再開含む）→ complete → analyze 投入 */
  start(): Promise<void>;
}

export const useImportStore = create<ImportStore>((set, get) => ({
  runs: {},
  async start() {
    const session = useProjectStore.getState().current;
    if (!session) return;
    const id = session.project.id;
    if (get().runs[id]?.status === "running") return;
    const put = (r: Partial<ImportRun>) =>
      set((s) => ({
        runs: { ...s.runs, [id]: { ...(s.runs[id] ?? { status: "running", progress: null, error: null }), ...r } },
      }));
    put({ status: "running", progress: null, error: null });

    const { uc } = getServices();
    const backend = currentBackend();
    const store = useProjectStore.getState();
    try {
      const { apply, mediaId } = await uc.importMedia.execute({
        project: session.project,
        backend,
        onProgress: (progress) => put({ progress }),
        onCheckpoint: (fn) => store.update(fn),
      });
      store.update(apply);
      await store.saveNow();
      const project = useProjectStore.getState().current?.project;
      if (!project || project.id !== id) return;
      const res = await uc.submitJob.execute({ project, backend, type: "analyze", params: { mediaId } });
      useJobsStore.getState().accept(res);
      await useProjectStore.getState().saveNow();
      put({ status: "done" });
    } catch (e) {
      console.error("[import]", e);
      put({ status: "error", error: toAppError(e) });
    }
  },
}));
