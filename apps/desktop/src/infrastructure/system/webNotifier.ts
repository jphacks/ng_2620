// NotifierPort のブラウザ実装（#48、#28 の決定）。許可されていれば Web Notifications、
// 許可の有無にかかわらずタブのタイトルとアイコンにバッジを付ける（前面に戻ると消える）。画面内のトーストは呼び出し側が出す。
// 許可の要求はユーザー操作の中でだけ行う（Safari は必須）：osNotify の中では要求しない

import type { NotifierPort, NotifyPermission } from "@/application/ports/system";

interface NotificationLike {
  permission: NotificationPermission;
  requestPermission(cb?: (p: NotificationPermission) => void): Promise<NotificationPermission> | undefined;
  new (title: string, opts?: { body?: string; tag?: string }): { onclick: (() => void) | null; close(): void };
}

export interface WebNotifierEnv {
  win: Pick<Window, "addEventListener" | "focus">;
  doc: Document;
  Notification?: NotificationLike;
}

const BADGE = "● ";

export function createWebNotifier(env?: Partial<WebNotifierEnv>): NotifierPort {
  const win = env?.win ?? window;
  const doc = env?.doc ?? document;
  const N =
    env && "Notification" in env ? env.Notification : (globalThis as { Notification?: NotificationLike }).Notification;

  let badged = false;
  let baseTitle = "";
  let baseIcon: { el: HTMLLinkElement; href: string } | null = null;

  const iconLink = () => doc.querySelector<HTMLLinkElement>('link[rel~="icon"]');

  /** 元のアイコンに赤い点を重ねる。読み込めない・canvas が無いときはタイトルだけ */
  async function paintFavicon() {
    const el = iconLink();
    if (!el || !el.href) return;
    baseIcon ??= { el, href: el.href };
    try {
      const img = new Image();
      img.src = baseIcon.href;
      await img.decode();
      const c = doc.createElement("canvas");
      c.width = c.height = 32;
      const g = c.getContext("2d");
      if (!g || !badged) return;
      g.drawImage(img, 0, 0, 32, 32);
      g.fillStyle = "#ef4444";
      g.beginPath();
      g.arc(23, 9, 8, 0, Math.PI * 2);
      g.fill();
      el.href = c.toDataURL("image/png");
    } catch {
      // アイコンのバッジは飾りなので、失敗してもタイトルのバッジは残す
    }
  }

  function clearBadge() {
    if (!badged) return;
    badged = false;
    if (doc.title.startsWith(BADGE)) doc.title = doc.title.slice(BADGE.length);
    if (baseIcon) baseIcon.el.href = baseIcon.href;
  }

  function setBadge() {
    if (badged) return;
    badged = true;
    baseTitle = doc.title;
    doc.title = BADGE + baseTitle;
    void paintFavicon();
  }

  win.addEventListener("focus", clearBadge);
  doc.addEventListener("visibilitychange", () => {
    if (doc.visibilityState === "visible") clearBadge();
  });

  const permission = (): NotifyPermission => (N ? N.permission : "unsupported");

  return {
    permission,
    async requestPermission() {
      if (!N) return "unsupported";
      if (N.permission !== "default") return N.permission;
      try {
        // 古い Safari はコールバック形式で Promise を返さない
        const r = await new Promise<NotificationPermission>((resolve, reject) => {
          const maybe = N.requestPermission(resolve);
          if (maybe) maybe.then(resolve, reject);
        });
        return r;
      } catch {
        return N.permission;
      }
    },
    async osNotify(title, body) {
      setBadge();
      if (!N || N.permission !== "granted") return;
      try {
        const n = new N(title, { body, tag: "vvlog" });
        n.onclick = () => {
          win.focus();
          n.close();
        };
      } catch (e) {
        // 一部の環境（Android の Chrome など）は new Notification を許さない
        console.warn("[notify]", e);
      }
    },
  };
}
