// S4 書き出し（F11）：形式・解像度・画質・音声・保存先・反映する手修正の件数 → [書き出す] → 進捗（合成 → エンコード）→ [再生] [Finder で表示]

import { useQuery } from "@tanstack/react-query";
import { FolderOpen, Play, RefreshCw } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import type { QualityPreset } from "@/application/usecases";
import { isAbsolute, QUALITY_PRESETS } from "@/application/usecases";
import { isAppError } from "@/domain/errors";
import type { ExportSettings } from "@/domain/job";
import { currentStage, isTerminal, overallProgress } from "@/domain/job";
import { exportFileName, needsExport, overridesHash, renderState } from "@/domain/project";
import { errorText, jobErrorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { currentBackend, useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { useArtifactState, useJobsStore } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import { Badge, Button, ErrorNotice, Label, ProgressBar, Select, Spinner, Switch } from "@/presentation/shared/ui";
import { askNotifyPermission } from "@/presentation/shared/notifyPermission";

const T = ja.export;

export function ExportScreen({ onGoImport }: { onGoImport(): void }) {
  const { uc, platform, paths } = useServices();
  const session = useProjectStore((s) => s.current);
  const project = session?.project;
  const connected = useConnectionStore((s) => s.state.status === "connected");
  const accept = useJobsStore((s) => s.accept);
  const [resolution, setResolution] = useState<"full" | "proxy">("full");
  const [quality, setQuality] = useState<QualityPreset>("standard");
  const [audio, setAudio] = useState(true);
  const [dest, setDest] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const overrides = useQuery({
    queryKey: ["overrides", project?.id, project?.updatedAt],
    queryFn: () => uc.loadOverrides.execute(project!.id),
    enabled: !!project,
  });

  const lastRef = project?.jobs.exports.at(-1);
  const lastJob = useJobsStore((s) => (lastRef ? s.jobs[lastRef.jobId] : undefined));
  const download = useArtifactState(lastRef?.jobId, "export_video");

  if (!project || !session) return null;

  // サンプル（F12）：書き出し済みの動画を再生できる
  if (project.sample) {
    const video = project.sample.exportedVideo ? paths.resolve(project.id, project.sample.exportedVideo) : null;
    return (
      <Centered>
        <ErrorNotice tone="info" title={T.sample}>
          {video && (
            <Button icon={<Play size={14} />} onClick={() => void platform.shell.open(video)}>
              {T.playSample}
            </Button>
          )}
        </ErrorNotice>
      </Centered>
    );
  }

  const rstate = renderState(project);
  if (rstate === "none" || rstate === "failed") {
    return (
      <Centered>
        <ErrorNotice tone="info" title={T.noRender}>
          <Button onClick={onGoImport}>{T.goImport}</Button>
        </ErrorNotice>
      </Centered>
    );
  }

  const doc = overrides.data;
  const unitCount = doc?.items.filter((o) => o.kind === "unit").length ?? 0;
  const meta = project.meta;
  const full = meta?.full ?? { width: project.source.width, height: project.source.height };
  const hasAudio = meta?.hasAudio ?? project.source.hasAudio;
  const defaultRel = `exports/${exportFileName(project)}`;
  const destAbs = dest ?? paths.resolve(project.id, defaultRel);
  const running = !!lastJob && !isTerminal(lastJob.status);
  const settings: ExportSettings = {
    format: "mp4_h264",
    crf: QUALITY_PRESETS[quality],
    audio: hasAudio && audio,
    resolution,
  };

  const submit = async (rerenderFirst: boolean) => {
    askNotifyPermission();
    setSubmitting(true);
    try {
      let p = project;
      if (rerenderFirst) {
        accept(await uc.startRender.execute({ project: p, backend: currentBackend() }));
        p = useProjectStore.getState().current?.project ?? p;
      }
      accept(
        await uc.startExport.execute({
          project: p,
          backend: currentBackend(),
          settings,
          file: dest && isAbsolute(dest) ? dest : defaultRel,
        }),
      );
    } catch (e) {
      const t = errorText(e);
      toast({
        kind: "error",
        title: isAppError(e, "DEPENDENCY_NOT_READY") && e.detail?.reason === "needs_render" ? T.needsRender : t.title,
        body: t.body,
      });
    } finally {
      setSubmitting(false);
    }
  };

  const changeDest = async () => {
    const picked = await platform.dialog.saveFile({
      defaultPath: destAbs,
      filters: [{ name: "MP4", extensions: ["mp4"] }],
    });
    if (picked) setDest(picked.toLowerCase().endsWith(".mp4") ? picked : `${picked}.mp4`);
  };

  const stage = lastJob ? currentStage(lastJob) : null;
  const doneFile =
    lastRef?.status === "succeeded" && lastRef.file
      ? isAbsolute(lastRef.file)
        ? lastRef.file
        : paths.resolve(project.id, lastRef.file)
      : null;
  const finishedPath = download?.status === "ready" ? (download.path ?? doneFile) : doneFile;

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-2xl p-8">
        {rstate === "stale" && (
          <div className="mb-4">
            <ErrorNotice tone="warning" title={T.needsRender} body={T.needsRenderBody}>
              <Button
                size="sm"
                icon={<RefreshCw size={12} />}
                disabled={!connected || submitting}
                onClick={() => void submit(true)}
              >
                {T.renderAndExport}
              </Button>
            </ErrorNotice>
          </div>
        )}
        {doc && needsExport(project, overridesHash(doc)) && (
          <div className="mb-4">
            <Badge tone="warning">{T.needsExport}</Badge>
          </div>
        )}
        <div className="grid grid-cols-[7rem_1fr] items-center gap-x-4 gap-y-4 rounded-lg border border-border bg-panel p-5">
          <Label>{T.format}</Label>
          <div className="flex items-center gap-3">
            <Select
              value="mp4_h264"
              onChange={() => {}}
              options={[{ value: "mp4_h264", label: T.formatMp4 }]}
              aria-label={T.format}
            />
            <span className="text-xs text-faint">{T.formatsLater}</span>
          </div>
          <Label>{T.resolution}</Label>
          <Select<"full" | "proxy">
            aria-label={T.resolution}
            value={resolution}
            onChange={setResolution}
            options={[
              { value: "full", label: T.resFull(full.width, full.height) },
              ...(meta ? [{ value: "proxy" as const, label: T.resProxy(meta.proxy.width, meta.proxy.height) }] : []),
            ]}
          />
          <Label>{T.quality}</Label>
          <Select<QualityPreset>
            aria-label={T.quality}
            value={quality}
            onChange={setQuality}
            options={(Object.keys(QUALITY_PRESETS) as QualityPreset[]).map((q) => ({
              value: q,
              label: T.qualities[q],
            }))}
          />
          <Label>{T.audio}</Label>
          {hasAudio ? (
            <Switch checked={audio} onChange={setAudio} label={T.includeAudio} />
          ) : (
            <span className="text-sm text-faint">{T.noAudio}</span>
          )}
          <Label>{T.dest}</Label>
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate font-mono text-sm" title={destAbs}>
              {destAbs}
            </span>
            <Button size="sm" onClick={() => void changeDest()}>
              {ja.common.change}
            </Button>
          </div>
          <Label>{T.overridesLabel}</Label>
          <span>{overrides.isLoading ? <Spinner /> : T.overrides(unitCount)}</span>
        </div>

        {!connected && (
          <div className="mt-4">
            <ErrorNotice tone="warning" title={T.offline} />
          </div>
        )}

        <div className="mt-4 flex justify-end">
          <Button
            variant="primary"
            disabled={!connected || running || submitting || rstate === "stale"}
            onClick={() => void submit(false)}
            icon={submitting ? <Spinner /> : undefined}
          >
            {T.start} ▶
          </Button>
        </div>

        {lastJob && (
          <div className="mt-6 rounded-lg border border-border bg-panel p-4">
            {running && stage && (
              <>
                <div className="mb-2 flex justify-between text-sm">
                  <span>{T.progress(overallProgress(lastJob), ja.stage[stage.name])}</span>
                  <span className="text-muted">
                    {stage.etaSec !== undefined ? ja.time.eta(stage.etaSec) : ja.common.calculating}
                  </span>
                </div>
                <ProgressBar value={overallProgress(lastJob)} />
              </>
            )}
            {lastJob.status === "succeeded" && download?.status === "downloading" && (
              <div className="flex items-center gap-2 text-sm text-muted">
                <Spinner /> {T.downloading}
              </div>
            )}
            {lastJob.status === "succeeded" && finishedPath && download?.status !== "downloading" && (
              <div className="flex items-center gap-2">
                <span className="text-success">{T.done}:</span>
                <span className="min-w-0 flex-1 truncate font-mono text-sm">{finishedPath.split(/[\\/]/).pop()}</span>
                <Button size="sm" icon={<Play size={12} />} onClick={() => void platform.shell.open(finishedPath)}>
                  {T.play}
                </Button>
                <Button
                  size="sm"
                  icon={<FolderOpen size={12} />}
                  onClick={() => void platform.shell.reveal(finishedPath)}
                >
                  {T.reveal}
                </Button>
              </div>
            )}
            {lastJob.status === "failed" && <ErrorNotice {...jobErrorText(lastJob.error)} />}
            {download?.status === "error" && download.error && <ErrorNotice {...errorText(download.error)} />}
          </div>
        )}
      </div>
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-xl p-10">{children}</div>;
}
