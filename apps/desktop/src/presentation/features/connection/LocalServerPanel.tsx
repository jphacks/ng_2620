// 設定 > 接続 > この PC の GPU サーバー（F03 P1）：フォルダと Python の指定、起動・停止、ログ

import { useQuery } from "@tanstack/react-query";
import { FolderOpen, Play, Square } from "lucide-react";
import { useState } from "react";
import type { LocalServerConfig } from "@/domain/localServer";
import { defaultLocalServerConfig, validateLocalServer } from "@/domain/localServer";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import {
  Button,
  ConfirmDialog,
  ErrorNotice,
  Label,
  Spinner,
  StatusDot,
  Switch,
  TextInput,
} from "@/presentation/shared/ui";

const T = ja.connection.local;
const STATUS_INTERVAL_MS = 2000;

export function LocalServerPanel() {
  const { uc, platform, env } = useServices();
  const saved = useConnectionStore((s) => s.localServer);
  const starting = useConnectionStore((s) => s.localStarting);
  const saveLocal = useConnectionStore((s) => s.saveLocal);
  const startLocal = useConnectionStore((s) => s.startLocal);
  const stopLocal = useConnectionStore((s) => s.stopLocal);
  const [c, setC] = useState<LocalServerConfig>(saved ?? defaultLocalServerConfig());
  const [portText, setPortText] = useState(String(c.port));
  const [touched, setTouched] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const status = useQuery({
    queryKey: ["local-server-status"],
    queryFn: uc.localServerStatus,
    refetchInterval: STATUS_INTERVAL_MS,
    enabled: env.isTauri,
  });

  if (!env.isTauri) return <ErrorNotice tone="info" title={T.browserOnly} />;

  const config: LocalServerConfig = { ...c, port: Number(portText) };
  const problems = validateLocalServer(config);
  const running = status.data?.running ?? false;
  const exitCode = status.data?.exitCode ?? null;
  const log = status.data?.log ?? [];

  const pickDir = async () => {
    const dir = await platform.dialog.pickDirectory({ defaultPath: c.serverDir || undefined });
    if (dir) setC({ ...c, serverDir: dir });
  };
  const pickPython = async () => {
    const [file] = await platform.dialog.pickFiles({});
    if (file) setC({ ...c, pythonPath: file });
  };

  const run = async (action: () => Promise<void>) => {
    setTouched(true);
    setError(null);
    if (problems.length) return;
    try {
      await saveLocal(config);
      await action();
    } catch (e) {
      setError(e);
    } finally {
      void status.refetch();
    }
  };

  const onStart = () =>
    run(async () => {
      await startLocal();
      toast({ kind: "success", title: T.started });
    });

  const onStop = async () => {
    setError(null);
    try {
      await stopLocal();
      toast({ kind: "success", title: T.stopped });
    } catch (e) {
      setError(e);
    } finally {
      void status.refetch();
    }
  };

  const err = error ? errorText(error) : null;

  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <div className="text-lg font-semibold">{T.title}</div>
        <div className="mt-1 text-sm text-muted">{T.intro}</div>
      </div>

      <div className="grid grid-cols-[9rem_1fr] items-start gap-x-3 gap-y-3">
        <Label htmlFor="ls-dir" className="pt-1.5">
          {T.serverDir}
        </Label>
        <div>
          <div className="flex gap-2">
            <TextInput
              id="ls-dir"
              value={c.serverDir}
              onChange={(e) => setC({ ...c, serverDir: e.target.value })}
              placeholder={T.serverDirHint}
              spellCheck={false}
              disabled={running}
            />
            <Button icon={<FolderOpen size={14} />} onClick={() => void pickDir()} disabled={running}>
              {T.browse}
            </Button>
          </div>
          <div className="mt-1 text-xs text-faint">{T.serverDirNote}</div>
        </div>
        <Label htmlFor="ls-python" className="pt-1.5">
          {T.python}
        </Label>
        <div>
          <div className="flex gap-2">
            <TextInput
              id="ls-python"
              value={c.pythonPath}
              onChange={(e) => setC({ ...c, pythonPath: e.target.value })}
              placeholder={T.pythonHint}
              spellCheck={false}
              disabled={running}
            />
            <Button icon={<FolderOpen size={14} />} onClick={() => void pickPython()} disabled={running}>
              {T.browse}
            </Button>
          </div>
          <div className="mt-1 text-xs text-faint">{T.pythonNote}</div>
        </div>
        <Label htmlFor="ls-port" className="pt-1.5">
          {T.port}
        </Label>
        <TextInput
          id="ls-port"
          className="w-28"
          inputMode="numeric"
          value={portText}
          onChange={(e) => setPortText(e.target.value)}
          disabled={running}
        />
        <div />
        <Switch checked={c.autoStart} onChange={(autoStart) => setC({ ...c, autoStart })} label={T.autoStart} />
      </div>

      {touched && problems.length > 0 && <ErrorNotice title={problems.map((x) => T.problems[x]).join(" / ")} />}
      {err && <ErrorNotice title={err.title} body={err.body} />}

      <div className="flex flex-wrap items-center gap-3">
        {running ? (
          <Button icon={<Square size={14} />} onClick={() => setConfirmStop(true)}>
            {T.stop}
          </Button>
        ) : (
          <Button
            variant="primary"
            icon={starting ? <Spinner /> : <Play size={14} />}
            onClick={() => void onStart()}
            disabled={starting}
          >
            {starting ? T.starting : T.start}
          </Button>
        )}
        <Button
          onClick={() =>
            void run(async () => {
              toast({ kind: "success", title: T.saved });
            })
          }
          disabled={starting}
        >
          {ja.common.save}
        </Button>
        <span className="ml-auto flex items-center gap-1.5 text-sm text-muted">
          {starting ? (
            <>
              <Spinner /> {T.starting}
            </>
          ) : running ? (
            <>
              <StatusDot tone="success" /> {T.status.running(status.data?.pid ?? null)}
            </>
          ) : exitCode !== null ? (
            <>
              <StatusDot tone="danger" /> {T.status.exited(exitCode)}
            </>
          ) : (
            <>
              <StatusDot tone="muted" /> {T.status.stopped}
            </>
          )}
        </span>
      </div>

      <details open={!running && exitCode !== null} className="rounded border border-border">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium">{T.log}</summary>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all border-t border-border px-3 py-2 font-mono text-xs text-muted">
          {log.length ? log.join("\n") : T.logEmpty}
        </pre>
      </details>

      <ConfirmDialog
        open={confirmStop}
        onOpenChange={setConfirmStop}
        title={T.stopTitle}
        body={T.stopBody}
        confirmLabel={T.stop}
        danger
        onConfirm={() => void onStop()}
      />
    </div>
  );
}
