// ジョブの状態（F05）。SSE を購読して状態を更新し、成果物を cache/<jobId>/ に取り寄せ、完了・失敗を通知する

import { create } from "zustand";
import type { AppError } from "@/domain/errors";
import { toAppError } from "@/domain/errors";
import type { Artifact, Job } from "@/domain/job";
import { isTerminal } from "@/domain/job";
import type { ProjectMutation } from "@/domain/project";
import { syncJobStatus } from "@/domain/project";
import { jobErrorText } from "../errors";
import { ja } from "../i18n/ja";
import { getServices } from "../services";
import { currentBackend } from "./connectionStore";
import { useProjectStore } from "./projectStore";
import { toast } from "./toastStore";

export interface ArtifactState {
  status: "downloading" | "ready" | "error";
  received: number;
  total: number | null;
  path?: string;
  error?: AppError;
}

interface JobsStore {
  jobs: Record<string, Job>;
  artifacts: Record<string, Record<string, ArtifactState>>;
  /** SSE を諦めてポーリングしているジョブ */
  polling: Record<string, boolean>;
  /** 投入・復元したジョブを引き受ける（未完了なら購読、完了済みなら成果物を補う） */
  adopt(job: Job): void;
  /** ユースケースの結果（job と project への変更）を反映して引き受ける */
  accept(res: { job: Job; apply: ProjectMutation }): Job;
  cancel(jobId: string): Promise<void>;
  retry(jobId: string): Promise<Job | null>;
  stopAll(): void;
}

const subs = new Map<string, AbortController>();

function notify(kind: "success" | "error" | "info", title: string, body?: string, actions?: { label: string; run(): void }[]) {
  toast({ kind, title, body, actions });
  if (typeof document !== "undefined" && !document.hasFocus()) void getServices().platform.notifier.osNotify(title, body);
}

export const useJobsStore = create<JobsStore>((set, get) => {
  const setJob = (job: Job) => {
    set((s) => ({ jobs: { ...s.jobs, [job.jobId]: job } }));
    useProjectStore.getState().update((p) => syncJobStatus(p, job));
  };

  const setArtifact = (jobId: string, name: string, patch: Partial<ArtifactState>) =>
    set((s) => {
      const prev = s.artifacts[jobId]?.[name] ?? { status: "downloading" as const, received: 0, total: null };
      return { artifacts: { ...s.artifacts, [jobId]: { ...s.artifacts[jobId], [name]: { ...prev, ...patch } } } };
    });

  const fetchArtifact = async (jobId: string, a: Artifact) => {
    if (a.name === "export_video") return; // 書き出しは保存先へ直接（SaveExportResult）
    const session = useProjectStore.getState().current;
    if (!session || session.offline) return;
    const { uc } = getServices();
    setArtifact(jobId, a.name, { status: "downloading", received: 0, total: a.size || null, error: undefined });
    let last = 0;
    try {
      const path = await uc.fetchArtifact.execute({
        project: session.project,
        backend: currentBackend(),
        jobId,
        artifact: a,
        onProgress: (received, total) => {
          const now = Date.now();
          if (now - last > 200) {
            last = now;
            setArtifact(jobId, a.name, { received, total });
          }
        },
      });
      setArtifact(jobId, a.name, { status: "ready", path, received: a.size, total: a.size });
      if (a.name === "meta" || a.name === "persons") {
        const cur = useProjectStore.getState().current;
        if (!cur || cur.project.id !== session.project.id) return;
        const apply = await uc.loadAnalysisResult.execute({ project: cur.project, jobId, name: a.name, defaultLabel: ja.cast.defaultLabel });
        useProjectStore.getState().update(apply);
      }
    } catch (e) {
      console.error(`[artifact] ${jobId}/${a.name}`, e);
      setArtifact(jobId, a.name, { status: "error", error: toAppError(e) });
    }
  };

  const syncArtifacts = async (job: Job) => {
    try {
      const list = await currentBackend().listArtifacts(job.jobId);
      await Promise.all(list.map((a) => fetchArtifact(job.jobId, a)));
    } catch (e) {
      console.warn("[artifact] 一覧を取得できません", e);
    }
  };

  const saveExport = async (job: Job) => {
    const session = useProjectStore.getState().current;
    if (!session) return;
    setArtifact(job.jobId, "export_video", { status: "downloading", received: 0, total: null });
    try {
      const { path, apply } = await getServices().uc.saveExportResult.execute({
        project: session.project,
        backend: currentBackend(),
        job,
        onProgress: (received, total) => setArtifact(job.jobId, "export_video", { received, total }),
      });
      useProjectStore.getState().update(apply);
      setArtifact(job.jobId, "export_video", { status: "ready", path });
      const file = path.split(/[\\/]/).pop() ?? path;
      notify("success", ja.notify.exportDone(file), undefined, [{ label: ja.export.reveal, run: () => void getServices().platform.shell.reveal(path) }]);
    } catch (e) {
      setArtifact(job.jobId, "export_video", { status: "error", error: toAppError(e) });
      const t = jobErrorText({ code: toAppError(e).code, message: toAppError(e).message });
      notify("error", t.title, t.body);
    }
  };

  const onFinished = async (job: Job) => {
    const type = ja.jobType[job.type];
    if (job.status === "succeeded") {
      if (job.type === "analyze") notify("success", ja.notify.analyzeDone(useProjectStore.getState().current?.project.persons.length ?? 0));
      if (job.type === "render") notify("success", ja.notify.renderDone);
      if (job.type === "export") await saveExport(job);
    } else if (job.status === "failed") {
      const t = jobErrorText(job.error);
      notify("error", `${ja.notify.failed(type)}：${t.title}`, t.body, [{ label: ja.common.retry, run: () => void get().retry(job.jobId) }]);
    } else if (job.status === "canceled") {
      notify("info", ja.notify.canceled(type));
    }
  };

  const subscribe = (job: Job) => {
    if (subs.has(job.jobId)) return;
    const ctrl = new AbortController();
    subs.set(job.jobId, ctrl);
    const projectId = useProjectStore.getState().current?.project.id;
    getServices()
      .uc.subscribeJob.execute({
        backend: currentBackend(),
        jobId: job.jobId,
        signal: ctrl.signal,
        handlers: {
          onJob: (j) => {
            if (useProjectStore.getState().current?.project.id === projectId) setJob(j);
          },
          onArtifact: (a) => fetchArtifact(job.jobId, a),
          onFallback: () => set((s) => ({ polling: { ...s.polling, [job.jobId]: true } })),
        },
      })
      .then((final) => {
        if (!ctrl.signal.aborted) return onFinished(final);
      })
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        console.error(`[job] ${job.jobId}`, e);
        const t = jobErrorText({ code: toAppError(e).code, message: toAppError(e).message });
        notify("error", t.title, t.body);
      })
      .finally(() => {
        if (subs.get(job.jobId) === ctrl) subs.delete(job.jobId);
      });
  };

  return {
    jobs: {},
    artifacts: {},
    polling: {},

    adopt(job) {
      set((s) => ({ jobs: { ...s.jobs, [job.jobId]: job } }));
      if (!isTerminal(job.status)) subscribe(job);
      else if (job.status === "succeeded") void syncArtifacts(job);
    },

    accept(res) {
      useProjectStore.getState().update(res.apply);
      get().adopt(res.job);
      return res.job;
    },

    async cancel(jobId) {
      await getServices().uc.cancelJob.execute({ backend: currentBackend(), jobId });
    },

    async retry(jobId) {
      const job = get().jobs[jobId];
      const session = useProjectStore.getState().current;
      if (!job || !session) return null;
      try {
        return get().accept(await getServices().uc.retryJob.execute({ project: session.project, backend: currentBackend(), job }));
      } catch (e) {
        const t = jobErrorText({ code: toAppError(e).code, message: toAppError(e).message });
        notify("error", t.title, t.body);
        return null;
      }
    },

    stopAll() {
      for (const c of subs.values()) c.abort();
      subs.clear();
      set({ jobs: {}, artifacts: {}, polling: {} });
    },
  };
});

/** プロジェクトに記録された種類のジョブ（無ければ null） */
export function useProjectJob(type: "analyze" | "render"): Job | null {
  const jobId = useProjectStore((s) => s.current?.project.jobs[type]?.jobId);
  return useJobsStore((s) => (jobId ? (s.jobs[jobId] ?? null) : null));
}

export function useArtifactState(jobId: string | undefined, name: string): ArtifactState | null {
  return useJobsStore((s) => (jobId ? (s.artifacts[jobId]?.[name] ?? null) : null));
}
