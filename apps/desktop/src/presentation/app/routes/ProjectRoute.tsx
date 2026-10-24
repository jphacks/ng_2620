// /project/:projectId/:workspace — 開いているプロジェクトのワークスペース（取り込み / 編集 / 書き出し）。
// ⌘1/2/3・⌘E、接続の切り替え提案、サーバーのデータ消失の案内、再接続時の復元

import { useEffect } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { ExportScreen } from "@/presentation/features/export";
import { ProfileMismatchDialog } from "@/presentation/features/connection";
import type { Workspace } from "@/presentation/shared/commands/registry";
import { useCommand } from "@/presentation/shared/commands/useCommands";
import { ja } from "@/presentation/shared/i18n/ja";
import { useAppStore } from "@/presentation/shared/stores/appStore";
import { useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, Dialog } from "@/presentation/shared/ui";
import { EditRoute } from "./EditRoute";
import { ImportWizard } from "./ImportWizard";

const WORKSPACES: Workspace[] = ["import", "edit", "export"];

function ServerMissingDialog() {
  const navigate = useNavigate();
  const restore = useProjectStore((s) => s.restore);
  const reset = useProjectStore((s) => s.resetServerData);
  const projectId = useProjectStore((s) => s.current?.project.id);
  const open = restore === "server_missing";
  return (
    <Dialog
      open={open}
      onOpenChange={() => useProjectStore.setState({ restore: "none" })}
      title={ja.project.serverMissingTitle}
      description={ja.project.serverMissingBody}
      footer={
        <>
          <Button onClick={() => useProjectStore.setState({ restore: "none" })}>{ja.project.later}</Button>
          <Button
            variant="primary"
            onClick={() => {
              reset();
              navigate(`/project/${projectId}/import`);
            }}
          >
            {ja.project.redo}
          </Button>
        </>
      }
    />
  );
}

export function ProjectRoute() {
  const { projectId, workspace } = useParams();
  const navigate = useNavigate();
  const current = useProjectStore((s) => s.current?.project.id);
  const restore = useProjectStore((s) => s.restore);
  const restoreJobs = useProjectStore((s) => s.restoreJobs);
  const connected = useConnectionStore((s) => s.state.status === "connected");
  const setLocation = useAppStore((s) => s.setLocation);
  const ws = WORKSPACES.includes(workspace as Workspace) ? (workspace as Workspace) : "import";

  useEffect(() => {
    setLocation(projectId ?? null, ws);
    return () => setLocation(null, null);
  }, [projectId, ws, setLocation]);

  // 開いたときにサーバーへ届かなかった → つながったら照合し直す
  useEffect(() => {
    if (connected && restore === "offline") void restoreJobs();
  }, [connected, restore, restoreJobs]);

  const go = (w: Workspace) => projectId && navigate(`/project/${projectId}/${w}`);
  useCommand({
    id: "workspace.import",
    keys: ["mod+1"],
    scope: "project",
    label: ja.workspace.import,
    run: () => go("import"),
  });
  useCommand({
    id: "workspace.edit",
    keys: ["mod+2"],
    scope: "project",
    label: ja.workspace.edit,
    run: () => go("edit"),
  });
  useCommand({
    id: "workspace.export",
    keys: ["mod+3", "mod+e"],
    scope: "project",
    label: ja.workspace.export,
    run: () => go("export"),
  });

  if (!projectId || current !== projectId) return <Navigate to="/" replace />;

  return (
    <>
      {ws === "import" && <ImportWizard />}
      {ws === "edit" && <EditRoute />}
      {ws === "export" && <ExportScreen onGoImport={() => go("import")} />}
      <ProfileMismatchDialog />
      <ServerMissingDialog />
    </>
  );
}
