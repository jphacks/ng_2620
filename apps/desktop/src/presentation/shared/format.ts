// 表示用の書式

import { ja } from "./i18n/ja";

/** 0:03（m:ss） */
export function formatClock(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** フレーム区間を「0:00–0:09」に */
export function formatFrameRange(first: number, last: number, fps: number): string {
  const f = fps > 0 ? fps : 30;
  return `${formatClock(first / f)}–${formatClock(last / f)}`;
}

export function formatRelative(iso: string, now = new Date()): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const min = Math.floor((now.getTime() - t) / 60_000);
  if (min < 1) return ja.time.justNow;
  if (min < 60) return ja.time.minutesAgo(min);
  const h = Math.floor(min / 60);
  if (h < 24) return ja.time.hoursAgo(h);
  const d = Math.floor(h / 24);
  return d === 1 ? ja.time.yesterday : ja.time.daysAgo(d);
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isFinite(d.getTime()) ? d.toLocaleString("ja-JP") : iso;
}

export function formatPercent(r: number): string {
  return `${Math.round(Math.min(1, Math.max(0, r)) * 100)}%`;
}

export function formatVram(mb: number | null): string {
  if (mb === null) return ja.common.unknown;
  return mb >= 1024 ? `${(mb / 1024).toFixed(mb % 1024 ? 1 : 0)} GB` : `${mb} MB`;
}
