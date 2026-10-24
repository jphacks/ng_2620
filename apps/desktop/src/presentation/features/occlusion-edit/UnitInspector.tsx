// インスペクタ（F10）：選択中の遮蔽物と、置換した人物ごとの前後（自動 / 前 / 後ろ）、手修正の一覧

import type { OccluderUnit } from "@/domain/occlusion";
import { autoDecision, defaultRange, explainWhy, modeKey, resolveModes } from "@/domain/occlusion";
import type { OverrideMode, UnitOverride } from "@/domain/overrides";
import { useEditorStore } from "@/presentation/shared/stores/editorStore";
import { formatTimecode } from "@/presentation/shared/format";
import { personColor } from "@/presentation/shared/colors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Badge, Button, cx, IconButton } from "@/presentation/shared/ui";
import { Redo2, Trash2, Undo2 } from "lucide-react";

export function UnitInspector() {
  const data = useEditorStore((s) => s.data);
  const selectedUnit = useEditorStore((s) => s.selectedUnit);
  const overrides = useEditorStore((s) => s.overrides);
  const playhead = useEditorStore((s) => s.playhead);
  const saving = useEditorStore((s) => s.saving);
  const canUndo = useEditorStore((s) => s.canUndo);
  const canRedo = useEditorStore((s) => s.canRedo);
  const persons = useProjectStore((s) => s.current?.project.persons ?? []);
  if (!data) return null;
  const unit = data.units.find((u) => u.unitId === selectedUnit);
  const unitOverrides = overrides.filter((o): o is UnitOverride => o.kind === "unit");
  const labelOf = (p: number) => persons.find((x) => x.personId === p)?.label ?? `人物 ${p}`;
  const tc = (f: number) => formatTimecode(f, data.fps);

  return (
    <div className="flex h-full flex-col text-sm">
      <div className="flex items-center gap-1 border-b border-border px-3 py-2">
        <div className="text-xs font-semibold text-muted">{ja.editor.inspector}</div>
        <span className="ml-2 text-xs text-faint">{ja.editor.saving[saving]}</span>
        <IconButton
          label={ja.editor.undo}
          icon={<Undo2 size={14} />}
          className="ml-auto"
          disabled={!canUndo}
          onClick={() => useEditorStore.getState().undo()}
        />
        <IconButton
          label={ja.editor.redo}
          icon={<Redo2 size={14} />}
          disabled={!canRedo}
          onClick={() => useEditorStore.getState().redo()}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {!unit ? (
          <p className="text-xs text-faint">{selectedUnit === 0 ? ja.editor.noSelection : ja.editor.notAnObject}</p>
        ) : (
          <UnitSection
            unit={unit}
            persons={data.persons}
            labelOf={labelOf}
            overrides={unitOverrides}
            playhead={playhead}
            tc={tc}
          />
        )}
        <h3 className="mb-1 mt-4 text-xs font-semibold text-muted">{ja.editor.overridesTitle}</h3>
        {unitOverrides.length === 0 ? (
          <p className="text-xs text-faint">{ja.editor.noOverrides}</p>
        ) : (
          <ul className="space-y-1">
            {unitOverrides.map((o) => (
              <li
                key={o.id}
                className={cx(
                  "flex items-center gap-2 rounded px-2 py-1 text-xs hover:bg-raised",
                  o.unitId === selectedUnit && "bg-raised",
                )}
                onClick={() => {
                  useEditorStore.getState().select(o.unitId);
                  useEditorStore.getState().seek(o.range[0]);
                }}
              >
                <span className="font-mono">#{o.unitId}</span>
                <span style={{ color: personColor(o.personId) }}>{labelOf(o.personId)}</span>
                <Badge tone={o.mode === "front" ? "accent" : "warning"}>{ja.editor.modes[o.mode]}</Badge>
                <span className="font-mono text-faint">{ja.editor.range(tc(o.range[0]), tc(o.range[1]))}</span>
                <IconButton
                  label={ja.editor.deleteOverride}
                  icon={<Trash2 size={12} />}
                  className="ml-auto"
                  onClick={(e) => {
                    e.stopPropagation();
                    useEditorStore.getState().deleteOverride(o.id);
                  }}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function UnitSection({
  unit,
  persons,
  labelOf,
  overrides,
  playhead,
  tc,
}: {
  unit: OccluderUnit;
  persons: number[];
  labelOf: (p: number) => string;
  overrides: UnitOverride[];
  playhead: number;
  tc: (f: number) => string;
}) {
  const modes = resolveModes(overrides, playhead);
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <h3 className="font-semibold">{ja.editor.unitTitle(unit.unitId)}</h3>
        <span className="text-xs text-faint">{ja.editor.unitFrames(tc(unit.frames[0]), tc(unit.frames[1]))}</span>
      </div>
      <div className="mt-3 space-y-3">
        {persons.map((p) => {
          const d = unit.perPerson[String(p)];
          const auto = autoDecision(unit, p);
          const current = modes.get(modeKey(p, unit.unitId)) ?? null;
          const range = defaultRange(unit, p);
          const set = (m: OverrideMode | null) => {
            const st = useEditorStore.getState();
            st.selectPerson(p);
            st.setMode(p, unit.unitId, m, range);
          };
          return (
            <div
              key={p}
              className="rounded border border-border p-2"
              onClick={() => useEditorStore.getState().selectPerson(p)}
            >
              <div className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full" style={{ background: personColor(p) }} />
                <span className="font-medium">{labelOf(p)}</span>
                <span className="ml-auto text-xs text-faint">{ja.editor.autoIs(ja.editor.decisions[auto])}</span>
              </div>
              <div className="mt-1 text-xs text-muted">
                {explainWhy(d?.why ?? "", auto)}
                {d && auto !== "none" ? `・${ja.editor.confidence(d.confidence)}` : ""}
              </div>
              <div className="mt-2 flex gap-1">
                {(["auto", "front", "behind"] as const).map((m) => {
                  const active = m === "auto" ? current === null : current === m;
                  return (
                    <Button
                      key={m}
                      size="sm"
                      variant={active ? "primary" : "secondary"}
                      onClick={() => set(m === "auto" ? null : m)}
                    >
                      {ja.editor.modes[m]}
                    </Button>
                  );
                })}
              </div>
              <div className="mt-1 font-mono text-[11px] text-faint">{ja.editor.range(tc(range[0]), tc(range[1]))}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
