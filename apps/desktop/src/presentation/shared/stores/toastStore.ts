// トースト（前面での通知）。成功は 5 秒、失敗は閉じるまで残す（ux/interaction §6）

import { create } from "zustand";

export interface ToastAction {
  label: string;
  run(): void;
}

export interface Toast {
  id: number;
  kind: "success" | "error" | "info";
  title: string;
  body?: string;
  actions?: ToastAction[];
}

interface ToastState {
  toasts: Toast[];
  push(t: Omit<Toast, "id">): number;
  dismiss(id: number): void;
}

let seq = 0;

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push(t) {
    const id = ++seq;
    set({ toasts: [...get().toasts.slice(-4), { ...t, id }] });
    if (t.kind !== "error") setTimeout(() => get().dismiss(id), 5000);
    return id;
  },
  dismiss(id) {
    set({ toasts: get().toasts.filter((x) => x.id !== id) });
  },
}));

export const toast = (t: Omit<Toast, "id">) => useToastStore.getState().push(t);
