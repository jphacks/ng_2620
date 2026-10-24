// EditorMediaPort の実装。画像は toUrl（Tauri は asset プロトコル）→ fetch → createImageBitmap、
// 16bit の units は readBytes → fast-png でデコード（createImageBitmap だと 8bit に丸められる）

import { decode } from "fast-png";
import type { DecodedLabels, EditorMediaPort } from "@/application/ports/editor";
import type { LocalFilePort } from "@/application/ports/media";
import { AppError } from "@/domain/errors";

export class BrowserEditorMedia implements EditorMediaPort {
  constructor(private readonly files: LocalFilePort) {}

  async image(path: string): Promise<ImageBitmap> {
    const res = await fetch(this.files.toUrl(path));
    if (!res.ok) throw new AppError("LAYERS_MISSING", `画像を読めません：${path}`, { status: res.status });
    return createImageBitmap(await res.blob(), { premultiplyAlpha: "none", colorSpaceConversion: "none" });
  }

  async labels(path: string): Promise<DecodedLabels> {
    return decodeLabels(await this.files.readBytes(path));
  }
}

/** 16bit（または 8bit）グレースケール PNG → ラベル配列 */
export function decodeLabels(bytes: Uint8Array): DecodedLabels {
  const png = decode(bytes);
  const { width, height, channels } = png;
  const src = png.data;
  const data = new Uint16Array(width * height);
  if (channels === 1) data.set(src as ArrayLike<number>);
  else for (let i = 0; i < data.length; i++) data[i] = (src as ArrayLike<number>)[i * channels]!;
  return { width, height, data };
}
