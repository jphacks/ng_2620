// 通知の許可は、ジョブを始めるボタンの中（ユーザー操作の直後）で一度だけ求める。ブラウザは操作の外では許可しない（Safari は必須）

import { getServices } from "./services";

/** まだ決まっていなければ許可を求める。待たない（ジョブの開始を遅らせない）。ボタンのハンドラの先頭で呼ぶ */
export function askNotifyPermission(): void {
  const { notifier } = getServices().platform;
  if (notifier.permission() === "default") void notifier.requestPermission();
}
