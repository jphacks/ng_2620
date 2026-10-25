import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { ja, SAMPLE_AVATAR } from "./strings";
import { ASSETS } from "./global-setup";

export { ja, SAMPLE_AVATAR };

/** ブラウザへ File をドロップする（実際のドラッグ＆ドロップと同じ dragover → drop）。zone は [data-drop-zone] の何番目か */
export async function dropFile(page: Page, file: string, opts: { type?: string; zone?: number } = {}) {
  const bytes = readFileSync(`${ASSETS}/${file}`).toString("base64");
  const dt = await page.evaluateHandle(
    ([b64, name, type]) => {
      const bin = Uint8Array.from(atob(b64!), (c) => c.charCodeAt(0));
      const d = new DataTransfer();
      d.items.add(new File([bin], name!, { type: type! }));
      return d;
    },
    [bytes, file, opts.type ?? "application/octet-stream"] as const,
  );
  const box = await page
    .locator("[data-drop-zone]")
    .nth(opts.zone ?? 0)
    .boundingBox();
  if (!box) throw new Error("ドロップ先が見つかりません");
  const pos = { clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 };
  await page.dispatchEvent("body", "dragover", { dataTransfer: dt, ...pos });
  await page.dispatchEvent("body", "drop", { dataTransfer: dt, ...pos });
}

/** ホームの最近のプロジェクトの先頭を開く */
export async function openFirstRecent(page: Page, base: string) {
  await page.goto(`${base}#/`);
  await page.locator("li button").first().click();
}
