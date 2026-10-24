// エディタの状態（F09 / F10）：再生位置・表示・選択・手修正と履歴。手修正は変更から 0.5 秒で overrides.json に保存

import { create } from "zustand";
import type { EditorData } from "@/application/usecases/editor";
import { toAppError } from "@/domain/errors";
import type { AppError } from "@/domain/errors";
import { History } from "@/domain/history";
import type { Range } from "@/domain/occlusion";
import { removeRange, setRange, upsertOverride } from "@/domain/occlusion";
import type { OcclusionOverride, OverrideMode, UnitOverride } from "@/domain/overrides";
import { getServices } from "../services";

export type ViewMode = "composite" | "original" | "split";

export interface LayerVisibility {
  avatar: boolean;
  units: boolean;
  hide: boolean;
  mask: boolean;
}

interface EditorState {
  projectId: string | null;
  data: EditorData | null;
  loadError: AppError | null;
  playhead: number;
  playing: boolean;
  /** 再生の速さと向き（J / L で 1 → 2 → 4 倍、負は逆再生） */
  rate: number;
  view: ViewMode;
  split: number;
  /** `\` 押下中は元動画 */
  peekOriginal: boolean;
  layers: LayerVisibility;
  zoom: number; // 0 = 画面に合わせる
  pan: { x: number; y: number };
  hoverUnit: number;
  selectedUnit: number;
  /** インスペクタで修正の対象にする人物 */
  selectedPerson: number | null;
  altDown: boolean;
  overrides: OcclusionOverride[];
  canUndo: boolean;
  canRedo: boolean;
  saving: "saved" | "dirty" | "saving" | "error";

  open(projectId: string, data: EditorData, overrides: OcclusionOverride[]): void;
  fail(e: unknown): void;
  close(): void;
  seek(f: number): void;
  step(delta: number): void;
  setPlaying(v: boolean): void;
  /** J / L：同じ向きなら加速、逆向きなら 1 倍から */
  shuttle(dir: 1 | -1): void;
  setView(v: ViewMode): void;
  setSplit(v: number): void;
  setPeek(v: boolean): void;
  toggleLayer(k: keyof LayerVisibility): void;
  setZoom(z: number, pan?: { x: number; y: number }): void;
  setPan(p: { x: number; y: number }): void;
  setHover(u: number): void;
  select(u: number): void;
  selectPerson(p: number | null): void;
  setAlt(v: boolean): void;
  /** 人物・ユニットのモードを区間に設定（mode null = 自動に戻す） */
  setMode(personId: number, unitId: number, mode: OverrideMode | null, range: Range): void;
  setOverrideRange(id: string, range: Range): void;
  deleteOverride(id: string): void;
  undo(): void;
  redo(): void;
}

const SAVE_MS = 500;
let history = new History<OcclusionOverride[]>([]);
let saveTimer: ReturnType<typeof setTimeout> | null = null;
let seq = 0;
const newId = () => `ovr_${Date.now().toString(36)}${(seq++).toString(36)}`;

const units = (list: readonly OcclusionOverride[]) => list.filter((o): o is UnitOverride => o.kind === "unit");
const others = (list: readonly OcclusionOverride[]) => list.filter((o) => o.kind !== "unit");

export const useEditorStore = create<EditorState>((set, get) => {
  const scheduleSave = () => {
    set({ saving: "dirty" });
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      const { projectId, overrides } = get();
      if (!projectId) return;
      set({ saving: "saving" });
      try {
        await getServices().uc.saveOverrides.execute(projectId, { schemaVersion: 1, items: overrides });
        if (get().overrides === overrides) set({ saving: "saved" });
      } catch (e) {
        console.error("[overrides]", e);
        set({ saving: "error" });
      }
    }, SAVE_MS);
  };

  const commit = (next: OcclusionOverride[]) => {
    history.push(next);
    set({ overrides: next, canUndo: history.canUndo, canRedo: history.canRedo });
    scheduleSave();
  };

  const clamp = (f: number) => {
    const n = get().data?.frameCount ?? 1;
    return Math.max(0, Math.min(n - 1, Math.round(f)));
  };

  return {
    projectId: null,
    data: null,
    loadError: null,
    playhead: 0,
    playing: false,
    rate: 1,
    view: "composite",
    split: 0.5,
    peekOriginal: false,
    layers: { avatar: true, units: false, hide: false, mask: false },
    zoom: 0,
    pan: { x: 0, y: 0 },
    hoverUnit: 0,
    selectedUnit: 0,
    selectedPerson: null,
    altDown: false,
    overrides: [],
    canUndo: false,
    canRedo: false,
    saving: "saved",

    open(projectId, data, overrides) {
      history = new History(overrides);
      set({
        projectId, data, loadError: null, overrides, canUndo: false, canRedo: false, saving: "saved",
        playhead: Math.min(get().projectId === projectId ? get().playhead : 0, data.frameCount - 1),
        playing: false, hoverUnit: 0, selectedUnit: 0, selectedPerson: data.persons[0] ?? null,
      });
    },
    fail(e) {
      set({ data: null, loadError: toAppError(e) });
    },
    close() {
      if (saveTimer) clearTimeout(saveTimer);
      set({ projectId: null, data: null, playing: false, overrides: [] });
    },
    seek(f) {
      set({ playhead: clamp(f) });
    },
    step(delta) {
      set({ playhead: clamp(get().playhead + delta), playing: false });
    },
    setPlaying(v) {
      const { data, playhead } = get();
      if (v && data && playhead >= data.frameCount - 1) set({ playhead: 0 });
      set({ playing: v, rate: 1 });
    },
    shuttle(dir) {
      const { playing, rate } = get();
      const same = playing && Math.sign(rate) === dir;
      set({ playing: true, rate: same ? Math.max(-8, Math.min(8, rate * 2)) : dir });
    },
    setView: (view) => set({ view }),
    setSplit: (split) => set({ split: Math.max(0, Math.min(1, split)) }),
    setPeek: (peekOriginal) => set({ peekOriginal }),
    toggleLayer: (k) => set({ layers: { ...get().layers, [k]: !get().layers[k] } }),
    setZoom: (zoom, pan) => set({ zoom, ...(pan ? { pan } : {}) }),
    setPan: (pan) => set({ pan }),
    setHover: (hoverUnit) => {
      if (get().hoverUnit !== hoverUnit) set({ hoverUnit });
    },
    select: (selectedUnit) => set({ selectedUnit }),
    selectPerson: (selectedPerson) => set({ selectedPerson }),
    setAlt: (altDown) => {
      if (get().altDown !== altDown) set({ altDown });
    },
    setMode(personId, unitId, mode, range) {
      const cur = get().overrides;
      const list = units(cur);
      const next = mode
        ? upsertOverride(list, { id: newId(), kind: "unit", personId, unitId, range, mode, createdAt: new Date().toISOString() }, newId)
        : removeRange(list, personId, unitId, range, newId);
      commit([...others(cur), ...next]);
    },
    setOverrideRange(id, range) {
      const { overrides, data } = get();
      commit([...others(overrides), ...setRange(units(overrides), id, range, (data?.frameCount ?? 1) - 1)]);
    },
    deleteOverride(id) {
      commit(get().overrides.filter((o) => o.id !== id));
    },
    undo() {
      const overrides = history.undo();
      set({ overrides, canUndo: history.canUndo, canRedo: history.canRedo });
      scheduleSave();
    },
    redo() {
      const overrides = history.redo();
      set({ overrides, canUndo: history.canUndo, canRedo: history.canRedo });
      scheduleSave();
    },
  };
});
