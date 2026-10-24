// コマンドの登録（useCommand）と、キー入力の配送（useHotkeyDispatcher、AppRoot で 1 回だけ）

import { useEffect, useRef } from "react";
import { useAppStore } from "../stores/appStore";
import type { Command } from "./registry";
import { CommandRegistry, comboFromEvent } from "./registry";

export const IS_MAC =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

export const commandRegistry = new CommandRegistry(IS_MAC);

/** コマンドを登録する。run は最新のものが呼ばれる（毎レンダーで登録し直さない） */
export function useCommand(cmd: Omit<Command, "run"> & { run: () => void; enabled?: boolean }) {
  const runRef = useRef(cmd.run);
  runRef.current = cmd.run;
  const { id, scope, label, allowInInput, enabled = true } = cmd;
  const keys = cmd.keys.join("|");
  useEffect(() => {
    if (!enabled) return;
    return commandRegistry.register({
      id,
      keys: keys.split("|"),
      scope,
      label,
      allowInInput,
      run: () => runRef.current(),
    });
  }, [id, keys, scope, label, allowInInput, enabled]);
}

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

export function useHotkeyDispatcher() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || e.repeat) return;
      const combo = comboFromEvent(e, IS_MAC);
      const { projectId, workspace } = useAppStore.getState();
      const cmd = commandRegistry.resolve(combo, { projectOpen: !!projectId, workspace });
      if (!cmd) return;
      const hasMod = combo.includes("mod+") || combo.includes("ctrl+");
      if (isTyping(e.target) && !hasMod && !cmd.allowInInput) return;
      e.preventDefault();
      cmd.run();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
