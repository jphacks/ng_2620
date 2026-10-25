// ファイルのドロップ先（ux/interaction §4）。ウィンドウ全体のドロップを 1 か所で受け、
// 座標の下にある一番内側の [data-drop-zone] に配る。受け付けない場所では何もしない

import { useEffect, useId, useRef, useState } from "react";
import type { FileDropEvent } from "@/application/ports/system";
import { getServices } from "../services";

export type DropState = "idle" | "active" | "reject";

interface Zone {
  accept(paths: string[]): boolean;
  onDrop(paths: string[]): void;
  setState(s: DropState): void;
  disabled: boolean;
}

const zones = new Map<string, Zone>();
let unsubscribe: (() => void) | null = null;
let hovered: string | null = null;

function zoneAt(x: number, y: number): string | null {
  let el = document.elementFromPoint(x, y) as HTMLElement | null;
  while (el) {
    const id = el.dataset.dropZone;
    if (id && zones.has(id) && !zones.get(id)!.disabled) return id;
    el = el.parentElement;
  }
  return null;
}

function setHovered(id: string | null, state: DropState) {
  if (hovered && hovered !== id) zones.get(hovered)?.setState("idle");
  hovered = id;
  if (id) zones.get(id)?.setState(state);
}

function onEvent(ev: FileDropEvent) {
  if (ev.type === "leave") return setHovered(null, "idle");
  const id = zoneAt(ev.x, ev.y);
  const zone = id ? zones.get(id) : undefined;
  if (ev.type === "over") {
    // ブラウザではドロップするまでパスがわからないので受け付ける表示にする
    return setHovered(id, !zone ? "idle" : ev.paths.length === 0 || zone.accept(ev.paths) ? "active" : "reject");
  }
  setHovered(null, "idle");
  if (!zone || !zone.accept(ev.paths)) return;
  if (!ev.prepare) return zone.onDrop(ev.paths);
  // 取り込み（サーバーへの転送）が要る：受け付ける先だけで始め、できたパスを渡す。失敗の表示は prepare 側
  void ev
    .prepare()
    .then((paths) => paths.length > 0 && zone.onDrop(paths))
    .catch(() => {});
}

function ensureSubscribed() {
  if (!unsubscribe) unsubscribe = getServices().platform.drops.subscribe(onEvent);
}

/**
 * 返す props を要素に付けると、その要素がドロップ先になる。
 * accept はパスの一覧を受け付けるか（拡張子など）。onDrop には受け付けたパスだけが渡る
 */
export function useDropZone(opts: {
  accept: (path: string) => boolean;
  onDrop: (paths: string[]) => void;
  multiple?: boolean;
  disabled?: boolean;
}) {
  const id = useId();
  const [state, setState] = useState<DropState>("idle");
  const ref = useRef(opts);
  ref.current = opts;
  const disabled = !!opts.disabled;

  useEffect(() => {
    ensureSubscribed();
    zones.set(id, {
      accept: (paths) => {
        const ok = paths.filter((p) => ref.current.accept(p));
        return ok.length > 0 && (ref.current.multiple || paths.length === 1);
      },
      onDrop: (paths) => {
        const ok = paths.filter((p) => ref.current.accept(p));
        ref.current.onDrop(ref.current.multiple ? ok : ok.slice(0, 1));
      },
      setState,
      disabled,
    });
    return () => {
      zones.delete(id);
      if (hovered === id) hovered = null;
    };
  }, [id, disabled]);

  return {
    state,
    props: {
      "data-drop-zone": id,
      "data-drop-active": state === "active" ? "true" : undefined,
      "data-drop-reject": state === "reject" ? "true" : undefined,
    },
  };
}
