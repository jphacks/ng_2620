// 1 フレームの合成に要る画像の読み込み（F09）。LRU で持ち、再生ヘッドの先を先読みする。
// 1 フレーム ≒ 540x960 RGBA × (2 + 人物数 × 2)。持ちすぎないよう件数を絞る

import type { DecodedLabels, EditorMediaPort } from "@/application/ports/editor";
import type { EditorData } from "@/application/usecases/editor";

export interface FrameBundle {
  f: number;
  frame: ImageBitmap;
  clean: ImageBitmap;
  avatars: Map<number, ImageBitmap>;
  hides: Map<number, ImageBitmap>;
  units: DecodedLabels | null;
  mask: ImageBitmap | null;
}

const CAPACITY = 24;
const AHEAD = 6;

export class FrameLoader {
  private readonly cache = new Map<number, Promise<FrameBundle>>();
  private readonly done = new Map<number, FrameBundle>();
  private disposed = false;

  constructor(
    private readonly data: EditorData,
    private readonly media: EditorMediaPort,
    private readonly wantMask: () => boolean,
  ) {}

  /** 読み込み済みならすぐ返す（再生ループ用） */
  ready(f: number): FrameBundle | null {
    return this.done.get(f) ?? null;
  }

  get(f: number): Promise<FrameBundle> {
    let p = this.cache.get(f);
    if (p) {
      this.cache.delete(f);
      this.cache.set(f, p); // LRU：最近使ったものを末尾へ
      return p;
    }
    p = this.load(f);
    this.cache.set(f, p);
    p.then((b) => (this.disposed ? close(b) : this.done.set(f, b))).catch(() => this.cache.delete(f));
    this.evict();
    return p;
  }

  /** f の先 AHEAD フレームを読み始める */
  prefetch(f: number) {
    for (let k = 1; k <= AHEAD; k++) {
      const g = f + k;
      if (g < this.data.frameCount && !this.cache.has(g)) void this.get(g).catch(() => {});
    }
  }

  private async load(f: number): Promise<FrameBundle> {
    const { paths, persons } = this.data;
    const { media } = this;
    const [frame, clean, avatars, hides, units, mask] = await Promise.all([
      media.image(paths.frame(f)),
      media.image(paths.clean(f)),
      Promise.all(persons.map((p) => media.image(paths.avatar(p, f)))),
      Promise.all(persons.map((p) => media.image(paths.hide(p, f)).catch(() => null))),
      paths.units ? media.labels(paths.units(f)).catch(() => null) : Promise.resolve(null),
      paths.mask && this.wantMask() ? media.image(paths.mask(f)).catch(() => null) : Promise.resolve(null),
    ]);
    const hideMap = new Map<number, ImageBitmap>();
    hides.forEach((h, k) => h && hideMap.set(persons[k]!, h));
    return { f, frame, clean, avatars: new Map(persons.map((p, k) => [p, avatars[k]!])), hides: hideMap, units, mask };
  }

  private evict() {
    while (this.cache.size > CAPACITY) {
      const [f] = this.cache.keys();
      if (f === undefined) break;
      this.cache.delete(f);
      const b = this.done.get(f);
      if (b) close(b);
      this.done.delete(f);
    }
  }

  /** 表示中に追跡マスクの表示を切り替えたとき：読み込み済みを捨てて読み直させる */
  invalidate() {
    this.done.forEach(close);
    this.done.clear();
    this.cache.clear();
  }

  dispose() {
    this.disposed = true;
    this.invalidate();
  }
}

function close(b: FrameBundle) {
  b.frame.close();
  b.clean.close();
  b.avatars.forEach((x) => x.close());
  b.hides.forEach((x) => x.close());
  b.mask?.close();
}
