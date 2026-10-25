// エディタの左パネル「キャスト」：割当の読み取り表示と [再描画]（F06）

import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { renderState } from "@/domain/project";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { currentBackend, useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { useJobsStore } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import { Badge, Button } from "@/presentation/shared/ui";
import { askNotifyPermission } from "@/presentation/shared/notifyPermission";

export function CastPanel({ onEditCast }: { onEditCast(): void }) {
  const { uc } = useServices();
  const project = useProjectStore((s) => s.current?.project);
  const accept = useJobsStore((s) => s.accept);
  const connected = useConnectionStore((s) => s.state.status === "connected");
  const [busy, setBusy] = useState(false);
  if (!project) return null;
  const stale = renderState(project) === "stale";

  const rerender = async () => {
    askNotifyPermission();
    setBusy(true);
    try {
      accept(await uc.startRender.execute({ project, backend: currentBackend() }));
    } catch (e) {
      const t = errorText(e);
      toast({ kind: "error", title: t.title, body: t.body });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5 p-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-muted">▾ {ja.cast.panelTitle}</span>
        <Button size="sm" variant="ghost" onClick={onEditCast}>
          {ja.common.edit}
        </Button>
      </div>
      <ul className="space-y-1 text-sm">
        {project.persons.map((p) => {
          const c = project.cast.find((x) => x.personId === p.personId);
          const avatarName = c ? (project.avatars[c.avatarId]?.name ?? c.avatarId) : null;
          return (
            <li key={p.personId} className="truncate pl-3">
              {p.label} {avatarName ? `→ ${avatarName}` : <span className="text-faint">{ja.cast.realPerson}</span>}
            </li>
          );
        })}
      </ul>
      {stale && (
        <div className="flex items-center gap-2 pl-3 pt-1">
          <Badge tone="warning">{ja.cast.needsRender}</Badge>
          <Button
            size="sm"
            icon={<RefreshCw size={12} />}
            disabled={busy || !connected || project.sample !== null}
            onClick={() => void rerender()}
          >
            {ja.cast.rerender}
          </Button>
        </div>
      )}
    </div>
  );
}
