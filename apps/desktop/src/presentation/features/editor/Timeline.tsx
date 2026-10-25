// タイムライン（F09 / F10）：目盛・再生ヘッド、人物の出現区間、選択中の物の出現区間、手修正の帯（端のドラッグで区間変更）

import { useEffect, useRef, useState } from "react";
import type { UnitOverride } from "@/domain/overrides";
import { personColor } from "@/presentation/shared/colors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { cx } from "@/presentation/shared/ui";
import { useEditorStore } from "@/presentation/shared/stores/editorStore";

const LABEL_W = 96;
const ROW_H = 22;

export function Timeline() {
  const data = useEditorStore((s) => s.data);
  const playhead = useEditorStore((s) => s.playhead);
  const selectedUnit = useEditorStore((s) => s.selectedUnit);
  const overrides = useEditorStore((s) => s.overrides);
  const persons = useProjectStore((s) => s.current?.project.persons ?? []);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [zoom, setZoom] = useState(1); // 1 = 全体が幅に収まる

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [data]);

  if (!data) return <div className="p-3 text-sm text-faint">{ja.editor.timeline}</div>;
  const n = data.frameCount;
  const px = Math.max(1, ((width - 8) / n) * zoom); // 1 フレームの幅
  const total = n * px;
  const xOf = (f: number) => f * px;
  const frameAt = (clientX: number) => {
    const r = scrollRef.current!.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.floor((clientX - r.left + scrollRef.current!.scrollLeft) / px)));
  };
  const scrub = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    const st = useEditorStore.getState();
    st.setPlaying(false);
    st.seek(frameAt(e.clientX));
  };
  const scrubMove = (e: React.PointerEvent) => {
    if (e.buttons & 1) useEditorStore.getState().seek(frameAt(e.clientX));
  };
  const onWheel = (e: React.WheelEvent) => {
    if (!(e.metaKey || e.ctrlKey)) return;
    setZoom((z) => Math.max(1, Math.min(40, z * Math.exp(-e.deltaY * 0.01))));
  };

  const unit = data.units.find((u) => u.unitId === selectedUnit);
  const unitOverrides = overrides.filter((o): o is UnitOverride => o.kind === "unit");
  const shown = selectedUnit ? unitOverrides.filter((o) => o.unitId === selectedUnit) : unitOverrides;
  const sec = Math.max(1, Math.round(data.fps));
  const tickEvery = px * sec < 40 ? sec * 5 : sec;

  const rows: { key: string; label: string; content: React.ReactNode }[] = [
    ...persons
      .filter((p) => data.persons.includes(p.personId))
      .map((p) => ({
        key: `p${p.personId}`,
        label: p.label,
        content: <Bar from={p.firstFrame} to={p.lastFrame} x={xOf} color={personColor(p.personId)} />,
      })),
    {
      key: "unit",
      label: unit ? ja.editor.unitTitle(unit.unitId) : ja.editor.tracks.unit,
      content: unit ? (
        <Bar from={unit.frames[0]} to={unit.frames[1]} x={xOf} color="rgb(var(--rgb-text-muted))" />
      ) : null,
    },
    {
      key: "ovr",
      label: ja.editor.tracks.overrides,
      content: shown.map((o) => <OverrideBand key={o.id} o={o} x={xOf} px={px} n={n} />),
    },
  ];

  return (
    <div className="flex h-full flex-col text-xs">
      <div className="flex min-h-0 flex-1">
        <div className="shrink-0 border-r border-border" style={{ width: LABEL_W }}>
          <div className="h-6 border-b border-border" />
          {rows.map((r) => (
            <div
              key={r.key}
              className="truncate px-2 leading-[22px] text-muted"
              style={{ height: ROW_H }}
              title={r.label}
            >
              {r.label}
            </div>
          ))}
        </div>
        <div ref={scrollRef} className="relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden" onWheel={onWheel}>
          <div className="relative" style={{ width: total + 8 }}>
            <div
              className="relative h-6 cursor-ew-resize border-b border-border"
              onPointerDown={scrub}
              onPointerMove={scrubMove}
            >
              {Array.from({ length: Math.floor(n / tickEvery) + 1 }, (_, k) => k * tickEvery).map((f) => (
                <div
                  key={f}
                  className="absolute top-0 h-full border-l border-border pl-1 text-[10px] text-faint"
                  style={{ left: xOf(f) }}
                >
                  {Math.floor(f / sec / 60)}:{String(Math.floor(f / sec) % 60).padStart(2, "0")}
                </div>
              ))}
            </div>
            {rows.map((r) => (
              <div
                key={r.key}
                className="relative border-b border-border/50"
                style={{ height: ROW_H }}
                onPointerDown={scrub}
                onPointerMove={scrubMove}
              >
                {r.content}
              </div>
            ))}
            <div
              className="pointer-events-none absolute bottom-0 top-0 w-px bg-accent"
              style={{ left: xOf(playhead) + px / 2 }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function Bar({ from, to, x, color }: { from: number; to: number; x: (f: number) => number; color: string }) {
  return (
    <div
      className="absolute top-1 h-[14px] rounded-sm opacity-70"
      style={{ left: x(from), width: Math.max(2, x(to + 1) - x(from)), background: color }}
    />
  );
}

function OverrideBand({ o, x, px, n }: { o: UnitOverride; x: (f: number) => number; px: number; n: number }) {
  const selectedUnit = useEditorStore((s) => s.selectedUnit);
  const [draft, setDraft] = useState<[number, number] | null>(null);
  const range = draft ?? o.range;
  const startDrag = (edge: 0 | 1) => (e: React.PointerEvent) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    const x0 = e.clientX;
    const r0 = o.range;
    const move = (ev: PointerEvent) => {
      const d = Math.round((ev.clientX - x0) / px);
      const next: [number, number] =
        edge === 0
          ? [Math.min(r0[1], Math.max(0, r0[0] + d)), r0[1]]
          : [r0[0], Math.max(r0[0], Math.min(n - 1, r0[1] + d))];
      setDraft(next);
    };
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      const d = Math.round((ev.clientX - x0) / px);
      if (d !== 0)
        useEditorStore.getState().setOverrideRange(o.id, edge === 0 ? [r0[0] + d, r0[1]] : [r0[0], r0[1] + d]);
      setDraft(null);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const front = o.mode === "front";
  return (
    <div
      className={cx(
        "absolute top-0.5 flex h-[18px] items-center overflow-hidden rounded-sm text-[10px] text-white",
        front ? "bg-accent/80" : "bg-warning/80",
        selectedUnit === o.unitId && "ring-1 ring-white",
      )}
      style={{ left: x(range[0]), width: Math.max(4, x(range[1] + 1) - x(range[0])) }}
      title={`#${o.unitId} ${ja.editor.modes[o.mode]}（${personLabel(o.personId)}）`}
      onPointerDown={(e) => {
        e.stopPropagation();
        useEditorStore.getState().select(o.unitId);
        useEditorStore.getState().selectPerson(o.personId);
      }}
    >
      <span className="absolute left-0 top-0 h-full w-1.5 cursor-ew-resize bg-white/30" onPointerDown={startDrag(0)} />
      <span className="truncate px-2">
        #{o.unitId} {ja.editor.modes[o.mode]}
      </span>
      <span className="absolute right-0 top-0 h-full w-1.5 cursor-ew-resize bg-white/30" onPointerDown={startDrag(1)} />
    </div>
  );
}

function personLabel(personId: number): string {
  return (
    useProjectStore.getState().current?.project.persons.find((p) => p.personId === personId)?.label ??
    `人物 ${personId}`
  );
}
