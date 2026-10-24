// S1 ホーム（F01・F02・F12）：動画のドロップで新規、最近のプロジェクト、サンプルを開く、接続先

import { useQueryClient } from "@tanstack/react-query";
import { Clapperboard, FolderOpen, Sparkles, Upload } from "lucide-react";
import { useState } from "react";
import type { RecentEntry } from "@/application/ports/project";
import type { Project } from "@/domain/project";
import { errorText } from "@/presentation/shared/errors";
import { formatRelative } from "@/presentation/shared/format";
import { useDropZone } from "@/presentation/shared/hooks/useDropZone";
import { ja } from "@/presentation/shared/i18n/ja";
import { queryKeys, useRecentProjects } from "@/presentation/shared/queries";
import { useServices } from "@/presentation/shared/services";
import { activeProfile, currentBackend, useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import {
  Button,
  Checkbox,
  ConfirmDialog,
  ContextMenu,
  cx,
  ErrorNotice,
  Spinner,
  StatusDot,
} from "@/presentation/shared/ui";
import { useNewProject } from "./useNewProject";

const T = ja.home;

export function HomeScreen({
  onOpenProject,
  onCreated,
  onOpenConnection,
  onBrowse,
}: {
  onOpenProject(path: string): void;
  onCreated(p: Project): void;
  onOpenConnection(): void;
  onBrowse(): void;
}) {
  const { openSample } = useServices();
  const np = useNewProject(onCreated);
  const recent = useRecentProjects();
  const drop = useDropZone({ accept: np.accept, onDrop: ([p]) => p && void np.fromPath(p) });
  const busy = np.state.kind === "probing" || np.state.kind === "creating";
  const rejected = np.state.kind === "rejected" ? np.state.error : null;
  const items = recent.data ?? [];

  const onSample = async () => {
    if (!openSample) {
      toast({ kind: "info", title: T.sampleMissing });
      return;
    }
    try {
      onOpenProject((await openSample()).path);
    } catch (e) {
      const t = errorText(e);
      toast({ kind: "error", title: t.title, body: t.body });
    }
  };

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto grid max-w-6xl grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-8 p-8">
        <div className="flex flex-col gap-4">
          <div
            {...drop.props}
            data-drop-reject={rejected ? "true" : drop.props["data-drop-reject"]}
            className={cx(
              "flex min-h-[18rem] flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-8 text-center transition-colors",
              rejected
                ? "border-danger/70 bg-danger/5"
                : drop.state === "active"
                  ? "border-accent bg-accent/10"
                  : "border-border bg-panel",
            )}
          >
            {busy ? <Spinner size={32} /> : <Upload size={32} className="text-muted" />}
            <div className="text-lg">
              {np.state.kind === "probing" ? T.probing : np.state.kind === "creating" ? T.creating : T.dropTitle}
            </div>
            <div className="flex items-center gap-2 text-muted">
              {T.or}
              <Button variant="primary" onClick={() => void np.pick()} disabled={busy}>
                {T.pickFile}
              </Button>
            </div>
            <div className="text-sm text-faint">{T.limits}</div>
          </div>
          {rejected && (
            <ErrorNotice title={rejected.title} body={rejected.body}>
              <Button size="sm" onClick={() => void np.pick()}>
                {ja.errorActions.pickOtherVideo}
              </Button>
            </ErrorNotice>
          )}
          <div className="flex gap-2">
            <Button icon={<Sparkles size={14} />} onClick={() => void onSample()}>
              {T.openSample}
            </Button>
            <Button icon={<FolderOpen size={14} />} onClick={onBrowse}>
              {T.openProject}
            </Button>
          </div>
          <ConnectionLine onOpenConnection={onOpenConnection} />
        </div>

        <div className="flex min-h-0 flex-col">
          <div className="mb-3 font-semibold">{T.recent}</div>
          {recent.isLoading && <Spinner />}
          {!recent.isLoading && items.length === 0 && (
            <button
              type="button"
              onClick={() => void onSample()}
              className="flex items-start gap-3 rounded-lg border border-border bg-panel p-4 text-left hover:border-accent"
            >
              <Sparkles size={20} className="mt-0.5 text-accent" />
              <div>
                <div className="font-medium">{T.firstTime}</div>
                <div className="mt-1 text-sm text-muted">{T.firstTimeBody}</div>
              </div>
            </button>
          )}
          <ul className="space-y-2">
            {items.map((e) => (
              <RecentRow key={e.path} entry={e} onOpen={() => onOpenProject(e.path)} />
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function ConnectionLine({ onOpenConnection }: { onOpenConnection(): void }) {
  const profile = useConnectionStore(activeProfile);
  const status = useConnectionStore((s) => s.state.status);
  const gpu = useConnectionStore((s) => s.capabilities?.gpuName);
  const connected = !!profile && status === "connected";
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-muted">{T.connectedTo}:</span>
      <StatusDot tone={connected ? "success" : status === "reconnecting" ? "warning" : "muted"} />
      <span>{connected ? `${profile.name}${gpu ? `（${gpu}）` : ""}` : T.notConnected}</span>
      <Button size="sm" variant="ghost" onClick={onOpenConnection}>
        {connected ? ja.common.change : T.setupConnection}
      </Button>
    </div>
  );
}

function RecentRow({ entry, onOpen }: { entry: RecentEntry; onOpen(): void }) {
  const { uc, platform } = useServices();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const [alsoServer, setAlsoServer] = useState(false);

  const doDelete = async () => {
    try {
      await uc.deleteProject.execute({
        path: entry.path,
        alsoServer,
        backend: alsoServer ? currentBackend() : undefined,
      });
    } catch (e) {
      const t = errorText(e);
      toast({ kind: "error", title: t.title, body: t.body });
    }
    await qc.invalidateQueries({ queryKey: queryKeys.recent });
  };

  const askDelete = (server: boolean) => {
    setAlsoServer(server);
    setConfirm(true);
  };

  const phase = T.phase[entry.phase];
  const status = entry.overridesCount > 0 ? `${phase}・${T.overrides(entry.overridesCount)}` : phase;

  return (
    <li>
      <ContextMenu
        items={[
          { label: ja.common.open, onSelect: onOpen },
          { label: T.revealProject, onSelect: () => void platform.shell.reveal(entry.path) },
          { label: T.moveToTrash, danger: true, onSelect: () => askDelete(false) },
          { label: T.deleteServerToo, danger: true, disabled: entry.isSample, onSelect: () => askDelete(true) },
        ]}
      >
        <button
          type="button"
          onClick={onOpen}
          className="flex w-full items-center gap-3 rounded border border-border bg-panel p-2 text-left hover:border-accent"
        >
          {entry.thumb ? (
            <img
              src={platform.files.toUrl(entry.thumb)}
              alt=""
              className="h-14 w-14 shrink-0 rounded bg-raised object-cover"
              draggable={false}
            />
          ) : (
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded bg-raised text-faint">
              <Clapperboard size={22} />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate font-medium">{entry.name}</span>
              {entry.isSample && <span className="text-xs text-accent">{T.sample}</span>}
              <span className="ml-auto shrink-0 text-xs text-faint">{formatRelative(entry.updatedAt)}</span>
            </div>
            <div className="mt-0.5 truncate text-sm text-muted">{status}</div>
          </div>
        </button>
      </ContextMenu>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={T.deleteTitle(entry.name)}
        body={alsoServer ? T.deleteServerBody : T.deleteBody}
        confirmLabel={alsoServer ? T.deleteServerToo : T.moveToTrash}
        danger
        onConfirm={() => void doDelete()}
      >
        {!entry.isSample && <Checkbox checked={alsoServer} onChange={setAlsoServer} label={T.deleteServerToo} />}
      </ConfirmDialog>
    </li>
  );
}
