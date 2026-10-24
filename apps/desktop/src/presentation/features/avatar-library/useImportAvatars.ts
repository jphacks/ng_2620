// .vrm の取り込み（選択・ドロップ共通）。結果をトーストで知らせ、一覧を更新する

import { useQueryClient } from "@tanstack/react-query";
import type { Avatar } from "@/domain/avatar";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { queryKeys } from "@/presentation/shared/queries";
import { useServices } from "@/presentation/shared/services";
import { toast } from "@/presentation/shared/stores/toastStore";

export const isVrmPath = (p: string) => /\.vrm$/i.test(p);

export function useImportAvatars() {
  const { uc, platform } = useServices();
  const qc = useQueryClient();

  const importPaths = async (paths: string[]): Promise<Avatar[]> => {
    if (!paths.length) return [];
    const { imported, failed } = await uc.importAvatar.execute(paths);
    await qc.invalidateQueries({ queryKey: queryKeys.avatars });
    if (imported.length) toast({ kind: "success", title: ja.avatar.imported(imported.length) });
    if (failed.length) {
      const e = errorText(failed[0]!.error);
      toast({
        kind: "error",
        title: failed.length > 1 ? ja.avatar.importFailed(failed.length) : e.title,
        body: e.body,
      });
    }
    return imported;
  };

  const pickAndImport = async (multiple = true) => {
    const paths = await platform.dialog.pickFiles({ multiple, filters: [{ name: "VRM", extensions: ["vrm"] }] });
    return importPaths(paths);
  };

  return { importPaths, pickAndImport };
}
