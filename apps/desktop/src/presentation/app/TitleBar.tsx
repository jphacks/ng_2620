// タイトルバー（S0）：アプリ名・プロジェクト名・ワークスペースタブ（⌘1/2/3）・設定・ジョブ。ウィンドウのドラッグ領域を兼ねる

import { ListChecks, Settings } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, useNavigate } from "react-router-dom";
import { needsExport, overridesHash, renderState } from "@/domain/project";
import type { Workspace } from "@/presentation/shared/commands/registry";
import { IS_MAC } from "@/presentation/shared/commands/useCommands";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { useAppStore } from "@/presentation/shared/stores/appStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { cx, IconButton, Kbd, Tooltip } from "@/presentation/shared/ui";

const WORKSPACES: Workspace[] = ["import", "edit", "export"];
const MOD = IS_MAC ? "⌘" : "Ctrl+";

export function TitleBar() {
  const { uc } = useServices();
  const navigate = useNavigate();
  const project = useProjectStore((s) => s.current?.project);
  const toggleJobs = useAppStore((s) => s.toggleJobDrawer);
  const overrides = useQuery({
    queryKey: ["overrides", project?.id, project?.updatedAt],
    queryFn: () => uc.loadOverrides.execute(project!.id),
    enabled: !!project,
  });
  const stale = project ? renderState(project) === "stale" : false;
  const exportDirty = project && overrides.data ? needsExport(project, overridesHash(overrides.data)) : false;

  return (
    <header className="flex h-titlebar shrink-0 items-center gap-3 border-b border-border bg-panel pl-3 pr-2">
      <button type="button" onClick={() => navigate("/")} className="font-semibold tracking-wide text-fg">
        {ja.app.name}
      </button>
      {project && (
        <span className="max-w-[18rem] truncate text-muted" title={project.name}>
          {project.name}
        </span>
      )}
      <div className="flex flex-1 justify-center">
        {project && (
          <nav className="flex rounded border border-border bg-bg p-0.5" aria-label="workspace">
            {WORKSPACES.map((w, i) => (
              <Tooltip
                key={w}
                content={
                  <span>
                    {ja.workspace[w]} <Kbd>{`${MOD}${i + 1}`}</Kbd>
                  </span>
                }
              >
                <NavLink
                  to={`/project/${project.id}/${w}`}
                  className={({ isActive }) =>
                    cx(
                      "relative rounded-sm px-4 py-1 text-base",
                      isActive ? "bg-raised text-fg" : "text-muted hover:text-fg",
                    )
                  }
                >
                  {ja.workspace[w]}
                  {((w === "export" && (exportDirty || stale)) || (w === "edit" && stale)) && (
                    <span
                      className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-warning"
                      aria-label={stale ? ja.cast.needsRender : ja.export.needsExport}
                    />
                  )}
                </NavLink>
              </Tooltip>
            ))}
          </nav>
        )}
      </div>
      <IconButton label={`${ja.jobsPanel.title}（⌃J）`} icon={<ListChecks size={16} />} onClick={toggleJobs} />
      <IconButton
        label={`${ja.settings.title}（${MOD},）`}
        icon={<Settings size={16} />}
        onClick={() => navigate("/settings")}
      />
    </header>
  );
}
