// 設定 > アバターライブラリ（S6、F07）：グリッド表示、[.vrm を追加]（ドロップ・複数可）、詳細、削除

import { useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plus, Trash2, UserRound } from "lucide-react";
import { useState } from "react";
import type { Avatar } from "@/domain/avatar";
import { licenseBadge, modificationAllowed } from "@/domain/avatar";
import { formatDateTime } from "@/presentation/shared/format";
import { useDropZone } from "@/presentation/shared/hooks/useDropZone";
import { ja } from "@/presentation/shared/i18n/ja";
import { queryKeys, useAvatars } from "@/presentation/shared/queries";
import { useServices } from "@/presentation/shared/services";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, ConfirmDialog, Dialog, EmptyState, ErrorNotice, Spinner } from "@/presentation/shared/ui";
import { AvatarThumb } from "./AvatarThumb";
import { LicenseBadge } from "./license";
import { isVrmPath, useImportAvatars } from "./useImportAvatars";

const T = ja.avatar;

export function AvatarLibraryPanel() {
  const avatars = useAvatars();
  const { importPaths, pickAndImport } = useImportAvatars();
  const [detail, setDetail] = useState<Avatar | null>(null);
  const drop = useDropZone({ accept: isVrmPath, onDrop: (p) => void importPaths(p), multiple: true });

  return (
    <div className="flex h-full min-h-0 flex-col p-6" {...drop.props}>
      <div className="mb-4 flex items-center gap-3">
        <div className="text-sm text-muted">{T.dropHint}</div>
        <Button className="ml-auto" variant="primary" icon={<Plus size={14} />} onClick={() => void pickAndImport(true)}>
          {T.add}
        </Button>
      </div>
      {avatars.isLoading && <Spinner />}
      {avatars.data?.length === 0 && <EmptyState icon={<UserRound size={32} />} title={T.empty} />}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3 overflow-auto">
        {avatars.data?.map((a) => (
          <button key={a.avatarId} type="button" onClick={() => setDetail(a)} className="flex flex-col items-center gap-2 rounded border border-border bg-panel-2 p-3 text-center hover:border-accent">
            <AvatarThumb avatar={a} size={96} />
            <div className="w-full truncate font-medium">{a.meta.name}</div>
            <div className="w-full truncate text-xs text-muted">{a.meta.authors.join("、") || T.noAuthor}</div>
            <LicenseBadge avatar={a} />
          </button>
        ))}
      </div>
      {detail && <AvatarDetailDialog avatar={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

export function AvatarDetailDialog({ avatar, onClose }: { avatar: Avatar; onClose(): void }) {
  const { uc, platform } = useServices();
  const qc = useQueryClient();
  const usedHere = useProjectStore((s) => !!s.current?.project.cast.some((c) => c.avatarId === avatar.avatarId));
  const [confirm, setConfirm] = useState(false);
  const m = avatar.meta;
  const lic = m.license;
  const yesNo = (v: boolean | undefined) => (v === undefined ? ja.common.unknown : v ? T.allowed : T.notAllowed);
  const mod = modificationAllowed(m);

  const rows: [string, string][] = [
    [T.authors, m.authors.join("、") || T.noAuthor],
    [T.version, m.version],
    [T.license, lic.name ?? ja.common.unknown],
    [T.commercial, `${ja.avatar.badge[licenseBadge(m)]}${lic.commercial ? `（${lic.commercial}）` : ""}`],
    [T.avatarPermission, lic.avatarPermission ?? ja.common.unknown],
    [T.redistribution, yesNo(lic.redistribution)],
    [ja.avatar.modification(mod), lic.modification ?? "—"],
    [T.importedAt, formatDateTime(avatar.importedAt)],
    ["ID", avatar.avatarId],
  ];

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={m.name}
      width="w-[560px]"
      footer={
        <>
          <Button variant="ghost" className="mr-auto text-danger" icon={<Trash2 size={14} />} onClick={() => setConfirm(true)}>
            {T.remove}
          </Button>
          <Button onClick={onClose}>{ja.common.close}</Button>
        </>
      }
    >
      <div className="flex gap-4">
        <AvatarThumb avatar={avatar} size={128} />
        <dl className="grid flex-1 grid-cols-[8rem_1fr] gap-y-1.5 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd className="break-all">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      {lic.url && (
        <Button className="mt-4" size="sm" icon={<ExternalLink size={14} />} onClick={() => void platform.shell.openUrl(lic.url!)}>
          {T.licenseUrl}
        </Button>
      )}
      {usedHere && (
        <div className="mt-4">
          <ErrorNotice tone="warning" title={T.inUse} body={T.removeBody} />
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={T.removeTitle(m.name)}
        body={T.removeBody}
        confirmLabel={ja.common.remove}
        danger
        onConfirm={async () => {
          await uc.removeAvatar.execute(avatar.avatarId);
          await qc.invalidateQueries({ queryKey: queryKeys.avatars });
          onClose();
        }}
      />
    </Dialog>
  );
}
