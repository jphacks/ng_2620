// ドロップ取り込み（ローカルサーバーへの転送）の進行表示。完了・失敗はしばらく残して消す

import { create } from "zustand";
import type { DropUploadEvent } from "@/application/ports/system";

interface DropUploadState {
  items: DropUploadEvent[];
  update(ev: DropUploadEvent): void;
  dismiss(id: string): void;
}

export const useDropUploadStore = create<DropUploadState>((set, get) => ({
  items: [],
  update(ev) {
    const rest = get().items.filter((x) => x.id !== ev.id);
    set({ items: [...rest, ev] });
    if (ev.state === "done" || ev.state === "canceled") setTimeout(() => get().dismiss(ev.id), 2500);
  },
  dismiss(id) {
    set({ items: get().items.filter((x) => x.id !== id) });
  },
}));
