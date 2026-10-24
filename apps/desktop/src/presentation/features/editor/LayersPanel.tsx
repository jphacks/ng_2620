// 左パネルのレイヤー表示切替と表示モード（F09）

import { ja } from "@/presentation/shared/i18n/ja";
import { Checkbox, cx } from "@/presentation/shared/ui";
import type { ViewMode } from "./compositor/Compositor";
import type { LayerVisibility } from "@/presentation/shared/stores/editorStore";
import { useEditorStore } from "@/presentation/shared/stores/editorStore";

const LAYERS: (keyof LayerVisibility)[] = ["avatar", "units", "hide", "mask"];
const VIEWS: ViewMode[] = ["composite", "original", "split"];

export function LayersPanel() {
  const layers = useEditorStore((s) => s.layers);
  const view = useEditorStore((s) => s.view);
  const hasMask = useEditorStore((s) => !!s.data?.paths.mask);
  const hasUnits = useEditorStore((s) => !!s.data?.paths.units);
  return (
    <div className="p-2 text-sm">
      <div className="font-semibold text-muted">▾ {ja.editor.layers}</div>
      <div className="mt-1 flex rounded border border-border p-0.5 text-xs">
        {VIEWS.map((v) => (
          <button
            key={v}
            type="button"
            className={cx(
              "flex-1 rounded px-1 py-0.5",
              view === v ? "bg-accent text-accent-fg" : "text-muted hover:bg-raised",
            )}
            onClick={() => useEditorStore.getState().setView(v)}
          >
            {ja.editor.view[v]}
          </button>
        ))}
      </div>
      <div className="mt-2 space-y-1 pl-1">
        {LAYERS.map((k) => (
          <div key={k} className="flex items-center justify-between">
            <Checkbox
              checked={layers[k]}
              disabled={(k === "mask" && !hasMask) || (k === "units" && !hasUnits)}
              onChange={() => useEditorStore.getState().toggleLayer(k)}
              label={ja.editor.layerNames[k]}
            />
            {ja.editor.layerKeys[k] && (
              <span className="font-mono text-[10px] text-faint">{ja.editor.layerKeys[k]}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
