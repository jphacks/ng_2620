// 元に戻す / やり直し（F10）。状態のスナップショットを積む単純な履歴（手修正の一覧は小さいので差分にしない）

export class History<T> {
  private past: T[] = [];
  private future: T[] = [];

  constructor(
    private present: T,
    private readonly limit = 200,
  ) {}

  get current(): T {
    return this.present;
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  /** 新しい状態に進む（やり直しの履歴は消える） */
  push(next: T): T {
    if (next === this.present) return next;
    this.past.push(this.present);
    if (this.past.length > this.limit) this.past.shift();
    this.present = next;
    this.future = [];
    return next;
  }

  undo(): T {
    const prev = this.past.pop();
    if (prev === undefined) return this.present;
    this.future.push(this.present);
    this.present = prev;
    return prev;
  }

  redo(): T {
    const next = this.future.pop();
    if (next === undefined) return this.present;
    this.past.push(this.present);
    this.present = next;
    return next;
  }

  /** 履歴を捨てて状態を置き換える（プロジェクトを開いたとき） */
  reset(state: T): void {
    this.present = state;
    this.past = [];
    this.future = [];
  }
}
