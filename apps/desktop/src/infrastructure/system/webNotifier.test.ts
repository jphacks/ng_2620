// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { createWebNotifier } from "./webNotifier";

type Perm = NotificationPermission;

function fakeNotification(initial: Perm, onRequest?: (cb?: (p: Perm) => void) => Promise<Perm> | undefined) {
  const shown: { title: string; body?: string; tag?: string; closed: boolean }[] = [];
  const instances: { onclick: (() => void) | null }[] = [];
  class N {
    static permission: Perm = initial;
    static requestPermission(cb?: (p: Perm) => void) {
      if (onRequest) return onRequest(cb);
      N.permission = "granted";
      return Promise.resolve<Perm>("granted");
    }
    onclick: (() => void) | null = null;
    private rec: (typeof shown)[number];
    constructor(title: string, opts?: { body?: string; tag?: string }) {
      this.rec = { title, ...opts, closed: false };
      shown.push(this.rec);
      instances.push(this);
    }
    close() {
      this.rec.closed = true;
    }
  }
  return { N: N as never, Cls: N, shown, instances };
}

function setup(initial: Perm, onRequest?: Parameters<typeof fakeNotification>[1]) {
  document.head.innerHTML = '<title>vvlog</title><link rel="icon" href="data:image/png;base64,AAAA">';
  const win = Object.assign(new EventTarget(), { focus: vi.fn() }) as unknown as Window;
  const fake = fakeNotification(initial, onRequest);
  const notifier = createWebNotifier({ win, doc: document, Notification: fake.N });
  return { notifier, win, ...fake };
}

afterEach(() => {
  document.title = "";
  document.head.innerHTML = "";
});

describe("許可", () => {
  it("permission は Notification の状態を返し、未対応なら unsupported", () => {
    expect(setup("default").notifier.permission()).toBe("default");
    expect(setup("denied").notifier.permission()).toBe("denied");
    const none = createWebNotifier({ win: window, doc: document, Notification: undefined });
    expect(none.permission()).toBe("unsupported");
  });

  it("requestPermission：default のときだけ求める。決まっていれば求めない", async () => {
    const onRequest = vi.fn();
    const a = setup("granted", onRequest);
    expect(await a.notifier.requestPermission()).toBe("granted");
    const b = setup("denied", onRequest);
    expect(await b.notifier.requestPermission()).toBe("denied");
    expect(onRequest).not.toHaveBeenCalled();
    const c = setup("default");
    expect(await c.notifier.requestPermission()).toBe("granted");
    expect(c.Cls.permission).toBe("granted");
  });

  it("コールバック形式（Promise を返さない古い Safari）でも許可を受け取る", async () => {
    const { notifier, Cls } = setup("default", (cb) => {
      Cls.permission = "denied";
      setTimeout(() => cb?.("denied"), 0);
      return undefined;
    });
    expect(await notifier.requestPermission()).toBe("denied");
  });

  it("要求が例外になっても現在の状態を返す。未対応は unsupported", async () => {
    const { notifier } = setup("default", () => Promise.reject(new Error("blocked")));
    expect(await notifier.requestPermission()).toBe("default");
    const none = createWebNotifier({ win: window, doc: document, Notification: undefined });
    expect(await none.requestPermission()).toBe("unsupported");
  });
});

describe("osNotify", () => {
  it("許可されていれば通知を出し、押すとウィンドウを前面に出して閉じる", async () => {
    const { notifier, shown, instances, win } = setup("granted");
    await notifier.osNotify("書き出しが完了しました", "out.mp4");
    expect(shown).toEqual([{ title: "書き出しが完了しました", body: "out.mp4", tag: "vvlog", closed: false }]);
    expect(win.focus).not.toHaveBeenCalled();
    instances[0]!.onclick!(); // 通知を押す
    expect(win.focus).toHaveBeenCalledTimes(1);
    expect(shown[0]!.closed).toBe(true);
  });

  it("許可を求めない：default／denied では通知を出さず、要求もしない", async () => {
    const onRequest = vi.fn();
    for (const p of ["default", "denied"] as const) {
      const { notifier, shown } = setup(p, onRequest);
      await notifier.osNotify("t");
      expect(shown).toEqual([]);
    }
    expect(onRequest).not.toHaveBeenCalled();
  });

  it("通知が出せない環境（コンストラクタが例外）でも落ちない。バッジは付く", async () => {
    setup("granted"); // タイトルなどの準備
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const throwing = Object.assign(
      function () {
        throw new TypeError("Illegal constructor");
      },
      { permission: "granted" as Perm, requestPermission: () => Promise.resolve("granted" as Perm) },
    );
    const n = createWebNotifier({ win: window, doc: document, Notification: throwing as never });
    await expect(n.osNotify("t")).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalled();
    expect(document.title.startsWith("● ")).toBe(true);
  });
});

describe("タブのバッジ", () => {
  it("許可がなくても、タイトルに ● が付き、前面に戻ると消える", async () => {
    const { notifier, win } = setup("denied");
    await notifier.osNotify("完了");
    expect(document.title).toBe("● vvlog");
    win.dispatchEvent(new Event("focus"));
    expect(document.title).toBe("vvlog");
  });

  it("続けて通知しても ● は重ならない", async () => {
    const { notifier, win } = setup("default");
    await notifier.osNotify("1");
    await notifier.osNotify("2");
    expect(document.title).toBe("● vvlog");
    win.dispatchEvent(new Event("focus"));
    expect(document.title).toBe("vvlog");
  });

  it("タブが見える状態に戻ったときも消える", async () => {
    const { notifier } = setup("denied");
    await notifier.osNotify("x");
    expect(document.title).toBe("● vvlog");
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(document.title).toBe("vvlog");
  });

  it("消した後にまた通知すれば、また付く。アプリがタイトルを変えていても壊さない", async () => {
    const { notifier, win } = setup("denied");
    await notifier.osNotify("a");
    document.title = "別のタイトル"; // 先頭の ● が無くなった（別の処理が上書き）
    win.dispatchEvent(new Event("focus"));
    expect(document.title).toBe("別のタイトル");
    await notifier.osNotify("b");
    expect(document.title).toBe("● 別のタイトル");
  });
});
