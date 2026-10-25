// ブラウザ版の OS 機能（Tauri なしの開発用）。選んだ・落としたファイルは MemoryFs に登録してパスで扱う

import { AppError } from "@/domain/errors";
import type { DialogPort, FileDropEvent, FileDropPort, NotifierPort, ShellPort } from "@/application/ports/system";
import type { FileSystem } from "../fs/FileSystem";
import type { MemoryFs } from "../fs/memoryFs";

export function browserDialog(fs: MemoryFs): DialogPort {
  return {
    pickFiles({ filters, multiple }) {
      return new Promise((resolve) => {
        const input = document.createElement("input");
        input.type = "file";
        input.multiple = !!multiple;
        const exts = filters?.flatMap((f) => f.extensions) ?? [];
        if (exts.length) input.accept = exts.map((e) => `.${e}`).join(",");
        input.onchange = () => resolve(Array.from(input.files ?? [], (f) => fs.putFile(f)));
        input.oncancel = () => resolve([]);
        input.click();
      });
    },
    async pickDirectory() {
      return null; // ブラウザではフォルダを選べない（既定の保存先を使う）
    },
    async saveFile({ defaultPath }) {
      return defaultPath ?? null;
    },
  };
}

export function browserShell(fs: Pick<FileSystem, "toUrl">): ShellPort {
  return {
    async reveal(path) {
      console.info("[shell] Finder で表示（ブラウザ版では未対応）:", path);
    },
    async open(path) {
      const url = fs.toUrl(path);
      if (url) window.open(url, "_blank");
    },
    async openUrl(url) {
      window.open(url, "_blank", "noopener");
    },
  };
}

export function browserFileDrop(fs: MemoryFs): FileDropPort {
  return {
    subscribe(listener: (ev: FileDropEvent) => void) {
      const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
      const over = (e: DragEvent) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        listener({ type: "over", x: e.clientX, y: e.clientY, paths: [] });
      };
      const leave = (e: DragEvent) => {
        if (e.relatedTarget === null) listener({ type: "leave" });
      };
      const drop = (e: DragEvent) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        const paths = Array.from(e.dataTransfer?.files ?? [], (f) => fs.putFile(f));
        listener({ type: "drop", x: e.clientX, y: e.clientY, paths });
      };
      window.addEventListener("dragover", over);
      window.addEventListener("dragleave", leave);
      window.addEventListener("drop", drop);
      return () => {
        window.removeEventListener("dragover", over);
        window.removeEventListener("dragleave", leave);
        window.removeEventListener("drop", drop);
      };
    },
  };
}

export const browserNotifier: NotifierPort = {
  async osNotify(title, body) {
    if (typeof Notification === "undefined") return;
    if (Notification.permission === "default") await Notification.requestPermission();
    if (Notification.permission === "granted") new Notification(title, { body });
  },
};

/** フルモードの暫定：サーバー側のネイティブダイアログ（#46）ができるまで、選択系は使えない */
export const pendingDialog: DialogPort = {
  async pickFiles() {
    throw new AppError("NOT_IMPLEMENTED", "ファイルの選択は準備中です（#46）");
  },
  async pickDirectory() {
    throw new AppError("NOT_IMPLEMENTED", "フォルダの選択は準備中です（#46）");
  },
  async saveFile({ defaultPath }) {
    return defaultPath ?? null; // 保存先は既定の場所を使う
  },
};

/** フルモードの暫定：ドロップ取り込み（#47）ができるまで何も受けない */
export const pendingFileDrop: FileDropPort = {
  subscribe() {
    return () => {};
  },
};
