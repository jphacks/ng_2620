// 届いた結果のプレビュー（F05）：proxy_frames → フレームのスライドショー、track_masks → 色付きマスクの重ね表示、mesh_preview → 動画再生。
// highlightPersonId を渡すとその人物だけを強調する（キャストの行にカーソルを合わせたとき、F06）

import { useEffect, useMemo, useRef, useState } from "react";
import { frameFile } from "@/domain/artifacts";
import type { MediaMeta } from "@/domain/media";
import { hexToRgb, personColor } from "@/presentation/shared/colors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { useArtifactState, useProjectJob } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { cx, ProgressBar } from "@/presentation/shared/ui";

type Mode = "frames" | "masks" | "mesh";

const PREVIEW_FPS = 10;

export function ProgressivePreview({ highlightPersonId, prefer, className }: { highlightPersonId?: number | null; prefer?: Mode; className?: string }) {
  const job = useProjectJob("analyze");
  const meta = useProjectStore((s) => s.current?.project.meta ?? null);
  const frames = useArtifactState(job?.jobId, "proxy_frames");
  const masks = useArtifactState(job?.jobId, "track_masks");
  const mesh = useArtifactState(job?.jobId, "mesh_preview");

  const available: Mode[] = [];
  if (frames?.status === "ready") available.push("frames");
  if (frames?.status === "ready" && masks?.status === "ready") available.push("masks");
  if (mesh?.status === "ready") available.push("mesh");

  const [chosen, setChosen] = useState<Mode | null>(null);
  // 既定は届いた中で一番新しい段階。キャストの強調中はマスク
  const auto = highlightPersonId && available.includes("masks") ? "masks" : prefer && available.includes(prefer) ? prefer : available.at(-1);
  const mode = chosen && available.includes(chosen) ? chosen : auto;
  const downloading = [frames, masks, mesh].find((a) => a?.status === "downloading");

  return (
    <div className={cx("flex flex-col overflow-hidden rounded border border-border bg-black", className)}>
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border bg-panel-2 px-2 text-sm">
        <span className="mr-auto text-muted">{ja.analysis.previewTitle}</span>
        {(["frames", "masks", "mesh"] as const).map((m) => (
          <button
            key={m}
            type="button"
            disabled={!available.includes(m)}
            onClick={() => setChosen(m)}
            className={cx("rounded-sm px-2 py-0.5 disabled:text-faint", mode === m ? "bg-accent text-accent-fg" : "hover:bg-raised")}
          >
            {m === "frames" ? ja.analysis.previewFrames : m === "masks" ? ja.analysis.previewMasks : ja.analysis.previewMesh}
          </button>
        ))}
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center">
        {!mode && <div className="p-6 text-sm text-faint">{ja.analysis.previewEmpty}</div>}
        {mode === "mesh" && mesh?.path && <MeshVideo path={mesh.path} />}
        {(mode === "frames" || mode === "masks") && frames?.path && meta && (
          <FramePlayer framesDir={frames.path} masksDir={mode === "masks" ? masks?.path : undefined} meta={meta} highlightPersonId={highlightPersonId ?? null} />
        )}
        {downloading && (
          <div className="absolute inset-x-0 top-0">
            <ProgressBar value={downloading.total ? downloading.received / downloading.total : 0} indeterminate={!downloading.total} className="!h-0.5 !rounded-none" />
          </div>
        )}
      </div>
    </div>
  );
}

function MeshVideo({ path }: { path: string }) {
  const { platform } = useServices();
  return <video className="max-h-full max-w-full" src={platform.files.toUrl(path)} autoPlay loop muted playsInline />;
}

function FramePlayer({ framesDir, masksDir, meta, highlightPersonId }: { framesDir: string; masksDir?: string; meta: MediaMeta; highlightPersonId: number | null }) {
  const { platform } = useServices();
  const { files } = platform;
  const [f, setF] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const step = Math.max(1, Math.round(meta.fps / PREVIEW_FPS));
  const palette = useMemo(() => [1, 2, 3, 4].map((id) => hexToRgb(personColor(id))), []);

  useEffect(() => {
    const t = setInterval(() => setF((x) => (x + step >= meta.frameCount ? 0 : x + step)), 1000 / PREVIEW_FPS);
    return () => clearInterval(t);
  }, [meta.frameCount, step]);

  // マスクは canvas で着色して重ねる。asset URL の画像は canvas を汚すので、バイト列から ImageBitmap を作る
  useEffect(() => {
    if (!masksDir) return;
    let cancelled = false;
    void (async () => {
      try {
        const bytes = await files.readBytes(files.join(masksDir, frameFile(f, "png")));
        const bmp = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
        const canvas = canvasRef.current;
        if (cancelled || !canvas) return;
        canvas.width = bmp.width;
        canvas.height = bmp.height;
        const g = canvas.getContext("2d", { willReadFrequently: true })!;
        g.drawImage(bmp, 0, 0);
        const img = g.getImageData(0, 0, bmp.width, bmp.height);
        const d = img.data;
        for (let i = 0; i < d.length; i += 4) {
          const pid = d[i]!; // 8bit、値 = personId（0 = 背景）
          if (pid === 0) {
            d[i + 3] = 0;
            continue;
          }
          const [r, gg, b] = palette[(pid - 1) % 4]!;
          const dim = highlightPersonId !== null && pid !== highlightPersonId;
          d[i] = r;
          d[i + 1] = gg;
          d[i + 2] = b;
          d[i + 3] = dim ? 40 : 150;
        }
        g.putImageData(img, 0, 0);
      } catch {
        // そのフレームのマスクが無い（人物がいない）
        const canvas = canvasRef.current;
        canvas?.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [f, masksDir, files, palette, highlightPersonId]);

  const src = files.toUrl(files.join(framesDir, frameFile(f, "jpg")));
  return (
    <div className="relative flex h-full w-full items-center justify-center">
      <div className="relative max-h-full max-w-full" style={{ aspectRatio: `${meta.proxy.width} / ${meta.proxy.height}`, height: "100%" }}>
        <img src={src} alt="" className="absolute inset-0 h-full w-full object-contain" draggable={false} />
        {masksDir && <canvas ref={canvasRef} className="absolute inset-0 h-full w-full object-contain" />}
      </div>
      <div className="absolute bottom-1 right-2 font-mono text-xs text-white/70">
        {f + 1}/{meta.frameCount}
      </div>
    </div>
  );
}
