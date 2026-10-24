// シミュレーター用の成果物を canvas で合成する（動く円 2 人と四角の遮蔽物）。DOM が無い環境では null

import type { MediaMeta } from "@/domain/media";
import { frameFile } from "@/domain/artifacts";
import type { ZipEntry } from "../fs/storeZip";

export const SIM_META: MediaMeta = {
  durationSec: 2,
  frameCount: 60,
  fps: 30,
  full: { width: 540, height: 960 },
  proxy: { width: 270, height: 480 },
  proxyScale: 2,
  hasAudio: false,
  sourceCodec: "h264",
};

export const SIM_PERSONS = [
  { personId: 1, firstFrame: 0, lastFrame: 59, visibleRatio: 0.97, bestFrame: 30, color: "#4f8cff" },
  { personId: 2, firstFrame: 10, lastFrame: 50, visibleRatio: 0.41, bestFrame: 30, color: "#f0883e" },
];

export interface SimSynth {
  frames(meta: MediaMeta): Promise<ZipEntry[]>;
  masks(meta: MediaMeta): Promise<ZipEntry[]>;
  thumbs(): Promise<ZipEntry[]>;
}

function personAt(index: number, f: number, w: number, h: number) {
  return { x: w * (0.32 + 0.36 * index) + Math.sin(f / 9 + index) * w * 0.06, y: h * 0.52, r: w * 0.14 };
}

function visible(p: (typeof SIM_PERSONS)[number], f: number) {
  return f >= p.firstFrame && f <= p.lastFrame;
}

async function encode(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Uint8Array> {
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, type, quality));
  if (!blob) throw new Error("canvas を画像にできませんでした");
  return new Uint8Array(await blob.arrayBuffer());
}

export function canvasSynth(): SimSynth | null {
  if (typeof document === "undefined") return null;
  const make = (w: number, h: number) => {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    return { c, g: c.getContext("2d")! };
  };
  return {
    async frames(meta) {
      const { width: w, height: h } = meta.proxy;
      const { c, g } = make(w, h);
      const out: ZipEntry[] = [];
      for (let f = 0; f < meta.frameCount; f++) {
        const grad = g.createLinearGradient(0, 0, 0, h);
        grad.addColorStop(0, "#2b3a4a");
        grad.addColorStop(1, "#1a1f26");
        g.fillStyle = grad;
        g.fillRect(0, 0, w, h);
        SIM_PERSONS.forEach((p, i) => {
          if (!visible(p, f)) return;
          const { x, y, r } = personAt(i, f, w, h);
          g.fillStyle = "#c9b8a6";
          g.beginPath();
          g.arc(x, y - r * 1.4, r * 0.5, 0, Math.PI * 2);
          g.fill();
          g.fillRect(x - r * 0.6, y - r * 0.9, r * 1.2, r * 2.2);
        });
        g.fillStyle = "#6b4f3a"; // 机（遮蔽物）
        g.fillRect(w * 0.15, h * 0.68, w * 0.7, h * 0.12);
        g.fillStyle = "rgba(255,255,255,0.6)";
        g.font = `${Math.round(w / 18)}px sans-serif`;
        g.fillText(`SIM ${frameFile(f, "jpg")}`, 8, h - 10);
        out.push({ name: `frames/${frameFile(f, "jpg")}`, bytes: await encode(c, "image/jpeg", 0.85) });
      }
      return out;
    },
    async masks(meta) {
      const { width: w, height: h } = meta.proxy;
      const { c, g } = make(w, h);
      g.imageSmoothingEnabled = false;
      const out: ZipEntry[] = [];
      for (let f = 0; f < meta.frameCount; f++) {
        g.fillStyle = "#000";
        g.fillRect(0, 0, w, h);
        SIM_PERSONS.forEach((p, i) => {
          if (!visible(p, f)) return;
          const { x, y, r } = personAt(i, f, w, h);
          const v = p.personId;
          g.fillStyle = `rgb(${v},${v},${v})`; // 値 = personId（contracts §4 track_masks）
          g.beginPath();
          g.arc(x, y - r * 1.4, r * 0.5, 0, Math.PI * 2);
          g.fill();
          g.fillRect(x - r * 0.6, y - r * 0.9, r * 1.2, r * 2.2);
        });
        out.push({ name: `masks/${frameFile(f, "png")}`, bytes: await encode(c, "image/png") });
      }
      return out;
    },
    async thumbs() {
      const { c, g } = make(256, 256);
      const out: ZipEntry[] = [];
      for (const p of SIM_PERSONS) {
        g.fillStyle = "#1e1e1e";
        g.fillRect(0, 0, 256, 256);
        g.fillStyle = p.color;
        g.beginPath();
        g.arc(128, 96, 48, 0, Math.PI * 2);
        g.fill();
        g.fillRect(76, 150, 104, 106);
        g.fillStyle = "#fff";
        g.font = "bold 40px sans-serif";
        g.fillText(String(p.personId), 116, 112);
        out.push({ name: `person_thumbs/${p.personId}.jpg`, bytes: await encode(c, "image/jpeg", 0.9) });
      }
      return out;
    },
  };
}
