// ステータスバー左：接続状態（●緑 = 接続、●黄 = 再接続中、●灰 = オフライン）。クリックで接続設定

import { ja } from "@/presentation/shared/i18n/ja";
import { activeProfile, useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { StatusDot } from "@/presentation/shared/ui";

export function useConnectionSummary() {
  const profile = useConnectionStore(activeProfile);
  const status = useConnectionStore((s) => s.state.status);
  const gpu = useConnectionStore((s) => s.capabilities?.gpuName ?? null);
  const tone = status === "connected" ? "success" : status === "reconnecting" ? "warning" : "muted";
  const label = !profile
    ? ja.status.noProfile
    : status === "connected"
      ? `${profile.name}${gpu ? ` (${gpu})` : ""}`
      : status === "reconnecting"
        ? `${profile.name} — ${ja.status.reconnecting}`
        : status === "checking"
          ? `${profile.name} — ${ja.status.checking}`
          : `${profile.name} — ${ja.status.offline}`;
  return { profile, status, tone, label, gpu } as const;
}

export function ConnectionIndicator({ onOpenSettings }: { onOpenSettings(): void }) {
  const { tone, label } = useConnectionSummary();
  return (
    <button type="button" onClick={onOpenSettings} title={ja.status.openConnection} className="flex min-w-0 items-center gap-1.5 px-2 hover:bg-raised">
      <StatusDot tone={tone} />
      <span className="truncate">{label}</span>
    </button>
  );
}
