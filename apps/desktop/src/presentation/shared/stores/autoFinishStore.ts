// 自動仕上げ（取り込み画面で「既定のアバターで自動仕上げ」を選んだとき）：人物一覧が届いたら、映っている割合の大きい順に
// 上限までを既定のアバターに置き換えて描画を投入し、描画が終わってエディタを開いたら再生する。
// 選択（ON/OFF とアバター）はアプリ設定に保存し、実行中の状態はプロジェクトごとにメモリに持つ

import { create } from "zustand";
import type { AutoFinishSetting } from "@/application/ports/settings";
import { autoDraft } from "@/domain/cast";
import { errorText } from "../errors";
import { getServices } from "../services";
import { currentBackend } from "./connectionStore";
import { useJobsStore } from "./jobsStore";
import { useProjectStore } from "./projectStore";
import { toast } from "./toastStore";

/** waiting：人物一覧を待っている / starting：描画を投入中 / play：描画済みのエディタを開いたら再生する */
type Phase = "waiting" | "starting" | "play";

interface AutoFinishStore {
  choice: AutoFinishSetting;
  runs: Record<string, Phase>;
  load(): Promise<void>;
  setChoice(patch: Partial<AutoFinishSetting>): void;
  /** 解析を始めたプロジェクトで自動仕上げを有効にする（選択が ON でアバターがあるときだけ） */
  arm(projectId: string): void;
  /** エディタを開いたとき：このプロジェクトを再生するなら true を返し、状態を消す */
  takeAutoplay(projectId: string): boolean;
}

export const useAutoFinishStore = create<AutoFinishStore>((set, get) => ({
  choice: { enabled: false, avatarId: null },
  runs: {},

  async load() {
    const s = await getServices().settings.load();
    if (s.autoFinish) set({ choice: s.autoFinish });
  },

  setChoice(patch) {
    const choice = { ...get().choice, ...patch };
    set({ choice });
    const { settings } = getServices();
    void settings
      .load()
      .then((s) => settings.save({ ...s, autoFinish: choice }))
      .catch((e) => console.warn("[auto-finish] 設定を保存できません", e));
  },

  arm(projectId) {
    const { enabled, avatarId } = get().choice;
    if (!enabled || !avatarId) return;
    set((s) => ({ runs: { ...s.runs, [projectId]: "waiting" } }));
    void startIfReady();
  },

  takeAutoplay(projectId) {
    if (get().runs[projectId] !== "play") return false;
    setPhase(projectId, null);
    return true;
  },
}));

function setPhase(projectId: string, phase: Phase | null) {
  useAutoFinishStore.setState((s) => {
    const runs = { ...s.runs };
    if (phase) runs[projectId] = phase;
    else delete runs[projectId];
    return { runs };
  });
}

async function startIfReady() {
  const project = useProjectStore.getState().current?.project;
  const { runs, choice } = useAutoFinishStore.getState();
  if (!project || runs[project.id] !== "waiting" || project.persons.length === 0) return;
  if (project.jobs.render || !choice.avatarId) return setPhase(project.id, null);
  setPhase(project.id, "starting");
  const { uc } = getServices();
  try {
    const avatars = await uc.listAvatars.execute();
    const draft = autoDraft(project.persons, choice.avatarId);
    useProjectStore.getState().update(uc.updateCast.execute({ draft, avatars }));
    const cur = useProjectStore.getState().current?.project ?? project;
    useJobsStore.getState().accept(await uc.startRender.execute({ project: cur, backend: currentBackend(), draft }));
    setPhase(project.id, "play");
  } catch (e) {
    console.error("[auto-finish]", e);
    setPhase(project.id, null);
    const t = errorText(e);
    toast({ kind: "error", title: t.title, body: t.body });
  }
}

// 人物一覧（persons）が届いたら始める
useProjectStore.subscribe((s, prev) => {
  if (s.current?.project.persons !== prev.current?.project.persons) void startIfReady();
});
