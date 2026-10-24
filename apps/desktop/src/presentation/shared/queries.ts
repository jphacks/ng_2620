// サーバー状態・ローカルの一覧の取得（TanStack Query）。キーはここで揃える

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getServices, useServices } from "./services";
import { useConnectionStore } from "./stores/connectionStore";

export const queryKeys = {
  avatars: ["avatars"] as const,
  recent: ["recent-projects"] as const,
  queue: (profileId: string | null) => ["queue", profileId] as const,
};

export function useAvatars() {
  const { uc } = useServices();
  return useQuery({ queryKey: queryKeys.avatars, queryFn: () => uc.listAvatars.execute() });
}

export function useRecentProjects() {
  const { uc } = useServices();
  return useQuery({ queryKey: queryKeys.recent, queryFn: () => uc.listRecent.execute() });
}

/** サーバーのキュー（5 秒ごと、接続中だけ） */
export function useServerQueue(enabled = true) {
  const backend = useConnectionStore((s) => s.backend);
  const profileId = useConnectionStore((s) => s.activeProfileId);
  const connected = useConnectionStore((s) => s.state.status === "connected");
  return useQuery({
    queryKey: queryKeys.queue(profileId),
    queryFn: () => getServices().uc.getQueue.execute(backend!),
    enabled: enabled && connected && !!backend,
    refetchInterval: 5000,
  });
}

export function useInvalidate() {
  const qc = useQueryClient();
  return (key: readonly unknown[]) => qc.invalidateQueries({ queryKey: key });
}
