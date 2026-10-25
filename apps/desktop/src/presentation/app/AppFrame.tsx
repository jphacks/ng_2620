// アプリの枠（S0）：タイトルバー＋ワークスペース＋ステータスバー、ジョブパネル、トースト、全体のショートカット

import { useCallback } from "react";
import { Outlet, useNavigate } from "react-router-dom";
import type { Project } from "@/domain/project";
import { HomeScreen, useNewProject } from "@/presentation/features/home";
import { JobDrawer } from "@/presentation/features/jobs";
import { useCommand } from "@/presentation/shared/commands/useCommands";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useInvalidate, queryKeys } from "@/presentation/shared/queries";
import { useServices } from "@/presentation/shared/services";
import { useAppStore } from "@/presentation/shared/stores/appStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import { StatusBar } from "./StatusBar";
import { TitleBar } from "./TitleBar";
import { DropUploadHost } from "./DropUploadHost";
import { ToastHost } from "./ToastHost";

/** 開いたプロジェクトの既定のワークスペース：描画済みなら編集、それ以外は取り込み */
function defaultWorkspace(p: Project) {
  return p.jobs.render?.status === "succeeded" || p.sample ? "edit" : "import";
}

export function useProjectNavigation() {
  const navigate = useNavigate();
  const { platform } = useServices();
  const openAt = useProjectStore((s) => s.openAt);
  const invalidate = useInvalidate();

  const open = useCallback(
    async (path: string) => {
      try {
        const p = await openAt(path);
        await invalidate(queryKeys.recent);
        navigate(`/project/${p.id}/${defaultWorkspace(p)}`);
      } catch (e) {
        const t = errorText(e);
        toast({ kind: "error", title: `${ja.project.openFailed}：${t.title}`, body: t.body });
      }
    },
    [openAt, navigate, invalidate],
  );

  const browse = useCallback(async () => {
    const dir = await platform.dialog.pickDirectory();
    if (dir) await open(dir);
  }, [platform, open]);

  const created = useCallback(
    (p: Project) => {
      void invalidate(queryKeys.recent);
      navigate(`/project/${p.id}/import`);
    },
    [navigate, invalidate],
  );

  return { open, browse, created };
}

export function HomeRoute() {
  const navigate = useNavigate();
  const { open, browse, created } = useProjectNavigation();
  return (
    <HomeScreen
      onOpenProject={(p) => void open(p)}
      onCreated={created}
      onOpenConnection={() => navigate("/settings/connection")}
      onBrowse={() => void browse()}
    />
  );
}

function GlobalCommands() {
  const navigate = useNavigate();
  const { browse, created } = useProjectNavigation();
  const np = useNewProject(created);
  const saveNow = useProjectStore((s) => s.saveNow);
  const toggleJobs = useAppStore((s) => s.toggleJobDrawer);
  useCommand({ id: "app.save", keys: ["mod+s"], scope: "global", label: ja.commands.save, run: () => void saveNow() });
  useCommand({ id: "app.open", keys: ["mod+o"], scope: "global", label: ja.commands.open, run: () => void browse() });
  useCommand({
    id: "app.new",
    keys: ["mod+n"],
    scope: "global",
    label: ja.commands.newProject,
    run: () => {
      navigate("/");
      void np.pick();
    },
  });
  useCommand({ id: "app.jobs", keys: ["ctrl+j"], scope: "global", label: ja.commands.jobs, run: toggleJobs });
  useCommand({
    id: "app.settings",
    keys: ["mod+,"],
    scope: "global",
    label: ja.commands.settings,
    run: () => navigate("/settings"),
  });
  return null;
}

export function AppFrame() {
  return (
    <div className="flex h-full min-w-[1280px] flex-col">
      <TitleBar />
      <main className="min-h-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
      <StatusBar />
      <JobDrawer />
      <ToastHost />
      <DropUploadHost />
      <GlobalCommands />
    </div>
  );
}
