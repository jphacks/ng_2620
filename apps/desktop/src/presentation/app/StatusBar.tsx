// ステータスバー（S0）：接続状態 │ 実行中ジョブ │ キュー │ 保存状態 │ ⌃J

import { useNavigate } from "react-router-dom";
import { ConnectionIndicator } from "@/presentation/features/connection";
import { JobStatusSummary } from "@/presentation/features/jobs";
import { ja } from "@/presentation/shared/i18n/ja";
import { useAppStore } from "@/presentation/shared/stores/appStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { cx } from "@/presentation/shared/ui";

function SaveState() {
  const hasProject = useProjectStore((s) => !!s.current);
  const state = useProjectStore((s) => s.saveState);
  const saveNow = useProjectStore((s) => s.saveNow);
  if (!hasProject) return null;
  const label = {
    saved: ja.status.saved,
    dirty: ja.status.dirty,
    saving: ja.status.saving,
    error: ja.status.saveFailed,
  }[state];
  return (
    <button
      type="button"
      onClick={() => void saveNow()}
      className={cx("px-2 hover:bg-raised", state === "error" && "text-danger")}
    >
      {label}
    </button>
  );
}

export function StatusBar() {
  const navigate = useNavigate();
  const toggle = useAppStore((s) => s.toggleJobDrawer);
  return (
    <footer className="flex h-statusbar shrink-0 items-center gap-1 border-t border-border bg-panel px-1 text-xs text-muted">
      <ConnectionIndicator onOpenSettings={() => navigate("/settings/connection")} />
      <span className="text-faint">│</span>
      <JobStatusSummary />
      <span className="ml-auto text-faint">│</span>
      <SaveState />
      <span className="text-faint">│</span>
      <button type="button" onClick={toggle} className="px-2 hover:bg-raised">
        ⌃J
      </button>
    </footer>
  );
}
