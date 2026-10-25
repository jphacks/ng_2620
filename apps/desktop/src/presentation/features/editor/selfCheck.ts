// 合成の一致確認（F09 受け入れ条件、contracts §5）：WebGL の合成を、フィクスチャの正解画像と比べる。
// 開発用エディタ（/#/dev/editor?fixture=v0&check=1）から実行する

import type { EditorMediaPort } from "@/application/ports/editor";
import type { EditorData } from "@/application/usecases/editor";
import { diffRatio } from "@/domain/composite";
import { modeKey } from "@/domain/occlusion";
import { buildModeTable, Compositor } from "./compositor/Compositor";
import { FrameLoader } from "./frames";

export interface CheckResult {
  name: string;
  ratio: number;
  ok: boolean;
}

const THRESHOLD = 0.005; // |差| > 24/255 の画素が 0.5% 未満

async function pixelsOf(media: EditorMediaPort, path: string, w: number, h: number): Promise<Uint8ClampedArray> {
  const bmp = await media.image(path);
  const c = new OffscreenCanvas(w, h);
  const ctx = c.getContext("2d")!;
  ctx.drawImage(bmp, 0, 0);
  bmp.close();
  return ctx.getImageData(0, 0, w, h).data;
}

/** cases：正解画像のパスと、そのフレームで効かせる手修正（personId, unitId → mode） */
export async function runCompositeCheck(
  data: EditorData,
  media: EditorMediaPort,
  f: number,
  cases: { name: string; expected: string; modes: Record<string, "front" | "behind"> }[],
): Promise<CheckResult[]> {
  const loader = new FrameLoader(data, media, () => false);
  const b = await loader.get(f);
  const comp = new Compositor(new OffscreenCanvas(data.width, data.height));
  const order = (data.order[String(f)] ?? data.persons).filter((p) => b.avatars.has(p));
  comp.setFrame(
    {
      frame: b.frame,
      clean: b.clean,
      persons: order.map((p) => ({
        row: data.persons.indexOf(p),
        avatar: b.avatars.get(p)!,
        hide: b.hides.get(p) ?? null,
      })),
      units: b.units,
      mask: null,
    },
    data.width,
    data.height,
  );
  const maxUnit = data.units.reduce((m, u) => Math.max(m, u.unitId), 0);
  const out: CheckResult[] = [];
  for (const c of cases) {
    const t = buildModeTable(data.persons, maxUnit, (p, u) => c.modes[modeKey(p, u)] ?? null);
    comp.uploadModes(t.table, t.width, t.height);
    comp.render({
      view: "composite",
      split: 0.5,
      showAvatar: true,
      showUnits: false,
      showHide: false,
      showMask: false,
      hoverUnit: 0,
      selectedUnit: 0,
      highlightHover: false,
    });
    const got = comp.readPixels();
    const want = await pixelsOf(media, c.expected, data.width, data.height);
    const ratio = diffRatio(got, want);
    out.push({ name: c.name, ratio, ok: ratio < THRESHOLD });
  }
  comp.dispose();
  loader.dispose();
  return out;
}
