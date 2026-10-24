// ビューア（F09）：WebGL 合成、再生、ズーム・パン、分割表示、遮蔽物のホバー・選択（F10 のフック）

import { useEffect, useMemo, useRef, useState } from "react";
import type { EditorData } from "@/application/usecases/editor";
import type { UnitOverride } from "@/domain/overrides";
import { resolveModes, modeKey } from "@/domain/occlusion";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { EmptyState, Spinner } from "@/presentation/shared/ui";
import { buildModeTable, Compositor } from "./compositor/Compositor";
import { useEditorStore } from "@/presentation/shared/stores/editorStore";
import type { FrameBundle } from "./frames";
import { FrameLoader } from "./frames";
import { useEditorCommands } from "./useEditorCommands";
import { formatTimecode } from "@/presentation/shared/format";
import { useEditorSession } from "./useEditorSession";

const MIN_ZOOM = 0.25;
const MAX_ZOOM = 4;

function draw(c: Compositor, data: EditorData, b: FrameBundle, overrides: readonly UnitOverride[]) {
  const order = (data.order[String(b.f)] ?? data.persons).filter((p) => b.avatars.has(p));
  c.setFrame(
    {
      frame: b.frame,
      clean: b.clean,
      persons: order.map((p) => ({ row: data.persons.indexOf(p), avatar: b.avatars.get(p)!, hide: b.hides.get(p) ?? null })),
      units: b.units,
      mask: b.mask,
    },
    data.width,
    data.height,
  );
  const modes = resolveModes(overrides, b.f);
  const maxUnit = data.units.reduce((m, u) => Math.max(m, u.unitId), 0);
  const t = buildModeTable(data.persons, maxUnit, (p, u) => modes.get(modeKey(p, u)) ?? null);
  c.uploadModes(t.table, t.width, t.height);
}

export function Viewer() {
  useEditorSession();
  useEditorCommands();
  const data = useEditorStore((s) => s.data);
  const loadError = useEditorStore((s) => s.loadError);
  if (loadError) {
    const text = errorText(loadError);
    return <EmptyState title={text.title} body={text.body} />;
  }
  if (!data) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
        <Spinner /> {ja.editor.loading}
      </div>
    );
  }
  return <ViewerCanvas data={data} />;
}

function ViewerCanvas({ data }: { data: EditorData }) {
  const { editorMedia } = useServices();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const compRef = useRef<Compositor | null>(null);
  const bundleRef = useRef<FrameBundle | null>(null);
  const [glError, setGlError] = useState<string | null>(null);
  const [frameError, setFrameError] = useState<string | null>(null);
  const [box, setBox] = useState({ w: 1, h: 1 });
  const s = useEditorStore();
  const unitOverrides = useMemo(() => s.overrides.filter((o): o is UnitOverride => o.kind === "unit"), [s.overrides]);
  // 読み込み器は effect の中で作る（StrictMode の 2 回目の実行で、破棄済みの個体を使い回さない）
  const [loader, setLoader] = useState<FrameLoader | null>(null);
  useEffect(() => {
    const l = new FrameLoader(data, editorMedia, () => useEditorStore.getState().layers.mask);
    setLoader(l);
    return () => l.dispose();
  }, [data, editorMedia]);

  useEffect(() => {
    if (!canvasRef.current) return;
    try {
      compRef.current = new Compositor(canvasRef.current);
    } catch (e) {
      setGlError(e instanceof Error ? e.message : String(e));
    }
    return () => compRef.current?.dispose();
  }, []);

  // 表示の大きさ（フィットの計算用）
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const render = () => {
    const c = compRef.current;
    const b = bundleRef.current;
    if (!c || !b) return;
    const st = useEditorStore.getState();
    c.render({
      view: st.peekOriginal ? "original" : st.view,
      split: st.split,
      showAvatar: st.layers.avatar,
      showUnits: st.layers.units,
      showHide: st.layers.hide,
      showMask: st.layers.mask,
      hoverUnit: st.hoverUnit,
      selectedUnit: st.selectedUnit,
      highlightHover: st.altDown,
    });
  };

  // マスクの表示を切り替えたら読み直す（マスクは表示中だけ読む）
  useEffect(() => loader?.invalidate(), [loader, s.layers.mask]);

  // フレームの差し替え（停止中・シーク）
  useEffect(() => {
    if (s.playing || !loader) return;
    let alive = true;
    loader
      .get(s.playhead)
      .then((b) => {
        if (!alive || !compRef.current) return;
        bundleRef.current = b;
        draw(compRef.current, data, b, unitOverrides);
        render();
        setFrameError(null);
        loader.prefetch(s.playhead);
      })
      .catch((e: unknown) => {
        console.warn("[viewer] frame", s.playhead, e);
        if (alive) setFrameError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.playhead, s.playing, loader, unitOverrides, s.layers.mask]);

  // 表示だけの変更は描き直すだけ
   
  useEffect(render, [s.view, s.split, s.peekOriginal, s.layers, s.hoverUnit, s.selectedUnit, s.altDown]);

  // 再生ループ：経過時間からフレームを決め、読み込み済みなら描く（間に合わなければ前のフレームのまま）
  useEffect(() => {
    if (!s.playing || !loader) return;
    let raf = 0;
    const t0 = performance.now();
    const f0 = useEditorStore.getState().playhead;
    const tick = () => {
      const st = useEditorStore.getState();
      const f = Math.floor(f0 + ((performance.now() - t0) / 1000) * data.fps * st.rate);
      if (f >= data.frameCount || f < 0) {
        st.seek(f < 0 ? 0 : data.frameCount - 1);
        st.setPlaying(false);
        return;
      }
      loader.prefetch(f);
      const b = loader.ready(f) ?? (void loader.get(f).catch(() => {}), null);
      if (b && compRef.current && b !== bundleRef.current) {
        bundleRef.current = b;
        draw(compRef.current, data, b, unitOverrides);
        render();
        if (st.playhead !== f) st.seek(f);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.playing, s.rate, loader, unitOverrides]);

  // Alt と \ は押している間だけ
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      useEditorStore.getState().setAlt(e.altKey);
      if (e.key === "\\") useEditorStore.getState().setPeek(true);
    };
    const up = (e: KeyboardEvent) => {
      useEditorStore.getState().setAlt(e.altKey);
      if (e.key === "\\") useEditorStore.getState().setPeek(false);
    };
    const blur = () => {
      useEditorStore.getState().setAlt(false);
      useEditorStore.getState().setPeek(false);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, []);

  const fit = Math.min(box.w / data.width, box.h / data.height) * 0.96;
  const scale = s.zoom > 0 ? s.zoom : fit;

  /** 画面座標 → 画像の画素（範囲外は null） */
  const toImage = (clientX: number, clientY: number) => {
    const r = canvasRef.current?.getBoundingClientRect();
    if (!r) return null;
    const x = Math.floor(((clientX - r.left) / r.width) * data.width);
    const y = Math.floor(((clientY - r.top) / r.height) * data.height);
    return x >= 0 && y >= 0 && x < data.width && y < data.height ? { x, y } : null;
  };
  const unitAt = (clientX: number, clientY: number) => {
    const p = toImage(clientX, clientY);
    const u = bundleRef.current?.units;
    return p && u ? u.data[p.y * u.width + p.x] ?? 0 : 0;
  };

  const drag = useRef<{ kind: "pan" | "split"; x: number; y: number; pan: { x: number; y: number } } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    const st = useEditorStore.getState();
    const r = canvasRef.current?.getBoundingClientRect();
    const nearSplit = st.view === "split" && r && Math.abs(e.clientX - (r.left + r.width * st.split)) < 8;
    if (e.button === 1 || nearSplit) {
      (e.target as Element).setPointerCapture(e.pointerId);
      drag.current = { kind: nearSplit && e.button === 0 ? "split" : "pan", x: e.clientX, y: e.clientY, pan: st.pan };
      e.preventDefault();
      return;
    }
    if (e.button === 0) st.select(unitAt(e.clientX, e.clientY));
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    const st = useEditorStore.getState();
    if (d?.kind === "pan") st.setPan({ x: d.pan.x + e.clientX - d.x, y: d.pan.y + e.clientY - d.y });
    else if (d?.kind === "split") {
      const r = canvasRef.current!.getBoundingClientRect();
      st.setSplit((e.clientX - r.left) / r.width);
    } else st.setHover(unitAt(e.clientX, e.clientY));
  };
  const onPointerUp = () => {
    drag.current = null;
  };
  const onWheel = (e: React.WheelEvent) => {
    const st = useEditorStore.getState();
    if (e.ctrlKey || e.metaKey) {
      // ピンチ（トラックパッドは ctrlKey 付きの wheel）と ⌘ + ホイールで拡大縮小
      const z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, scale * Math.exp(-e.deltaY * 0.01)));
      st.setZoom(z);
    } else st.setPan({ x: st.pan.x - e.deltaX, y: st.pan.y - e.deltaY });
  };

  if (glError) return <EmptyState title="WebGL2 が使えません" body={glError} />;
  return (
    <div className="flex h-full flex-col">
      <div
        ref={wrapRef}
        className="relative min-h-0 flex-1 overflow-hidden"
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => useEditorStore.getState().setHover(0)}
      >
        <canvas
          ref={canvasRef}
          className="absolute left-1/2 top-1/2"
          style={{
            width: data.width * scale,
            height: data.height * scale,
            transform: `translate(calc(-50% + ${s.pan.x}px), calc(-50% + ${s.pan.y}px))`,
            imageRendering: scale > 1.5 ? "pixelated" : "auto",
            cursor: s.view === "split" ? "ew-resize" : s.hoverUnit ? "pointer" : "default",
          }}
        />
        {frameError && <div className="absolute inset-x-2 bottom-2 rounded bg-danger/80 px-2 py-1 text-xs text-white">{frameError}</div>}
        {s.peekOriginal && <div className="absolute left-2 top-2 rounded bg-overlay px-2 py-0.5 text-xs">{ja.editor.view.original}</div>}
      </div>
      <div className="flex h-7 shrink-0 items-center gap-3 border-t border-border px-3 font-mono text-xs text-muted">
        <span className="text-text">{formatTimecode(s.playhead, data.fps)}</span>
        <span>
          {s.playhead + 1} / {data.frameCount}
        </span>
        <span>{Math.round(scale * 100)}%</span>
        <span className="ml-auto truncate font-sans text-faint">{ja.editor.shortcutsHint}</span>
      </div>
    </div>
  );
}
