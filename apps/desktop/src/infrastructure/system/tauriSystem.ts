// Tauri 版の OS 機能：dialog / opener / notification プラグイン、ウィンドウへのファイルドロップ

import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open, save } from "@tauri-apps/plugin-dialog";
import { isPermissionGranted, requestPermission, sendNotification } from "@tauri-apps/plugin-notification";
import { openPath, openUrl, revealItemInDir } from "@tauri-apps/plugin-opener";
import type {
  DialogPort,
  FileDropEvent,
  FileDropPort,
  NotifierPort,
  NotifyPermission,
  ShellPort,
} from "@/application/ports/system";

export const tauriDialog: DialogPort = {
  async pickFiles({ filters, multiple }) {
    const r = await open({ multiple: !!multiple, directory: false, filters });
    if (!r) return [];
    return Array.isArray(r) ? r : [r];
  },
  async pickDirectory(opts) {
    const r = await open({ directory: true, multiple: false, defaultPath: opts?.defaultPath });
    return typeof r === "string" ? r : null;
  },
  async saveFile({ defaultPath, filters }) {
    return (await save({ defaultPath, filters })) ?? null;
  },
};

export const tauriShell: ShellPort = {
  reveal: (path) => revealItemInDir(path),
  open: (path) => openPath(path),
  openUrl: (url) => openUrl(url),
};

export const tauriFileDrop: FileDropPort = {
  subscribe(listener: (ev: FileDropEvent) => void) {
    let lastPaths: string[] = [];
    let unlisten: (() => void) | null = null;
    let disposed = false;
    const toCss = (p: { x: number; y: number }) => ({
      x: p.x / window.devicePixelRatio,
      y: p.y / window.devicePixelRatio,
    });
    void getCurrentWebview()
      .onDragDropEvent((e) => {
        const p = e.payload;
        if (p.type === "enter") {
          lastPaths = p.paths;
          listener({ type: "over", ...toCss(p.position), paths: lastPaths });
        } else if (p.type === "over") {
          listener({ type: "over", ...toCss(p.position), paths: lastPaths });
        } else if (p.type === "drop") {
          listener({ type: "drop", ...toCss(p.position), paths: p.paths });
          lastPaths = [];
        } else {
          listener({ type: "leave" });
          lastPaths = [];
        }
      })
      .then((fn) => {
        if (disposed) fn();
        else unlisten = fn;
      });
    return () => {
      disposed = true;
      unlisten?.();
    };
  },
};

export const tauriNotifier: NotifierPort = {
  async osNotify(title, body) {
    try {
      let granted = await isPermissionGranted();
      if (!granted) granted = (await requestPermission()) === "granted";
      if (granted) sendNotification({ title, body });
    } catch (e) {
      console.warn("[notify]", e);
    }
  },
  permission: (): NotifyPermission => "granted", // プラグインが必要なときに許可を求める（osNotify の中）
  requestPermission: async (): Promise<NotifyPermission> => "granted",
};
