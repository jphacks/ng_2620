// UI の設定（エディタのパネル寸法）。アプリ設定（settings.json）の panelLayouts に保存する（F15）

import type { PanelGroupStorage } from "react-resizable-panels";
import { getServices } from "../services";

let cache: Record<string, unknown> = {};
let timer: ReturnType<typeof setTimeout> | null = null;

export async function loadPrefs() {
  cache = { ...(await getServices().settings.load()).panelLayouts };
}

function scheduleSave() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(async () => {
    const { settings } = getServices();
    const s = await settings.load();
    await settings.save({ ...s, panelLayouts: { ...s.panelLayouts, ...cache } });
  }, 500);
}

/** react-resizable-panels の autoSaveId 用の保存先 */
export const panelStorage: PanelGroupStorage = {
  getItem(name) {
    const v = cache[name];
    return v === undefined ? null : JSON.stringify(v);
  },
  setItem(name, value) {
    try {
      cache[name] = JSON.parse(value) as unknown;
      scheduleSave();
    } catch {
      // 形式が違う値は保存しない
    }
  },
};
