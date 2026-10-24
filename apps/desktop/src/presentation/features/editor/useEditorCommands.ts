// エディタのショートカット（ux/interaction §1）。スコープは workspace:edit

import type { UnitOverride } from "@/domain/overrides";
import { defaultRange } from "@/domain/occlusion";
import { useCommand } from "@/presentation/shared/commands/useCommands";
import { useEditorStore } from "@/presentation/shared/stores/editorStore";

const S = () => useEditorStore.getState();
const scope = "workspace:edit" as const;

/** 選択中のユニット × 人物で、再生ヘッドにかかっている修正（I / O / Delete の対象） */
function currentOverride(): UnitOverride | null {
  const s = S();
  const list = s.overrides.filter((o): o is UnitOverride => o.kind === "unit" && o.unitId === s.selectedUnit && (s.selectedPerson === null || o.personId === s.selectedPerson));
  return [...list].reverse().find((o) => o.range[0] <= s.playhead && s.playhead <= o.range[1]) ?? list[list.length - 1] ?? null;
}

function setSelectedMode(mode: "front" | "behind" | null) {
  const s = S();
  const unit = s.data?.units.find((u) => u.unitId === s.selectedUnit);
  const person = s.selectedPerson ?? s.data?.persons[0];
  if (!unit || person === undefined) return;
  s.setMode(person, unit.unitId, mode, defaultRange(unit, person));
}

function zoomBy(k: number) {
  const s = S();
  const z = s.zoom > 0 ? s.zoom : 1;
  s.setZoom(Math.max(0.25, Math.min(4, z * k)));
}

export function useEditorCommands() {
  useCommand({ id: "editor.play", keys: ["space"], scope, run: () => S().setPlaying(!S().playing) });
  useCommand({ id: "editor.reverse", keys: ["j"], scope, run: () => S().shuttle(-1) });
  useCommand({ id: "editor.stop", keys: ["k"], scope, run: () => S().setPlaying(false) });
  useCommand({ id: "editor.forward", keys: ["l"], scope, run: () => S().shuttle(1) });
  useCommand({ id: "editor.prev", keys: ["left"], scope, run: () => S().step(-1) });
  useCommand({ id: "editor.next", keys: ["right"], scope, run: () => S().step(1) });
  useCommand({ id: "editor.prev10", keys: ["shift+left"], scope, run: () => S().step(-10) });
  useCommand({ id: "editor.next10", keys: ["shift+right"], scope, run: () => S().step(10) });
  useCommand({ id: "editor.home", keys: ["home"], scope, run: () => S().seek(0) });
  useCommand({ id: "editor.end", keys: ["end"], scope, run: () => S().seek((S().data?.frameCount ?? 1) - 1) });
  useCommand({ id: "editor.units", keys: ["u"], scope, run: () => S().toggleLayer("units") });
  useCommand({ id: "editor.hide", keys: ["h"], scope, run: () => S().toggleLayer("hide") });
  useCommand({ id: "editor.front", keys: ["f"], scope, run: () => setSelectedMode("front") });
  useCommand({ id: "editor.behind", keys: ["b"], scope, run: () => setSelectedMode("behind") });
  useCommand({ id: "editor.auto", keys: ["a"], scope, run: () => setSelectedMode(null) });
  useCommand({
    id: "editor.in",
    keys: ["i"],
    scope,
    run: () => {
      const o = currentOverride();
      if (o) S().setOverrideRange(o.id, [S().playhead, o.range[1]]);
    },
  });
  useCommand({
    id: "editor.out",
    keys: ["o"],
    scope,
    run: () => {
      const o = currentOverride();
      if (o) S().setOverrideRange(o.id, [o.range[0], S().playhead]);
    },
  });
  useCommand({
    id: "editor.delete",
    keys: ["delete", "backspace"],
    scope,
    run: () => {
      const o = currentOverride();
      if (o) S().deleteOverride(o.id);
    },
  });
  useCommand({ id: "editor.deselect", keys: ["escape"], scope, run: () => S().select(0) });
  useCommand({ id: "editor.undo", keys: ["mod+z"], scope, run: () => S().undo() });
  useCommand({ id: "editor.redo", keys: ["mod+shift+z"], scope, run: () => S().redo() });
  useCommand({ id: "editor.zoomIn", keys: ["mod+=", "mod++"], scope, run: () => zoomBy(1.25) });
  useCommand({ id: "editor.zoomOut", keys: ["mod+-"], scope, run: () => zoomBy(0.8) });
  useCommand({ id: "editor.fit", keys: ["mod+0"], scope, run: () => S().setZoom(0, { x: 0, y: 0 }) });
}
