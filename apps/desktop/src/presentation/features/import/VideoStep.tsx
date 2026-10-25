// S2-1 動画（F02）：プレビュー（元ファイルを再生）、長さ・解像度・fps・音声、送信先の接続名、[解析を始める]

import { Play } from "lucide-react";
import { useState } from "react";
import { fileNameOf } from "@/domain/validation";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { activeProfile, useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, ErrorNotice, Spinner } from "@/presentation/shared/ui";
import { useImportStore } from "./importStore";

const T = ja.importStep;

export function VideoStep({
  onCancel,
  onStarted,
  onOpenConnection,
}: {
  onCancel(): void;
  onStarted(): void;
  onOpenConnection(): void;
}) {
  const { platform } = useServices();
  const project = useProjectStore((s) => s.current?.project);
  const profile = useConnectionStore(activeProfile);
  const status = useConnectionStore((s) => s.state.status);
  const gpu = useConnectionStore((s) => s.capabilities?.gpuName);
  const start = useImportStore((s) => s.start);
  const running = useImportStore((s) => (project ? s.runs[project.id]?.status === "running" : false));
  const [videoError, setVideoError] = useState(false);
  if (!project) return null;
  const src = project.source;
  const resumable = !!project.compute.upload && !project.compute.mediaId;
  const canStart = !!profile && status === "connected" && !running;

  const begin = () => {
    void start();
    onStarted();
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_22rem] gap-6 p-6">
      <div className="flex min-h-0 items-center justify-center overflow-hidden rounded border border-border bg-black">
        {videoError ? (
          <div className="p-6 text-sm text-muted">{T.previewUnavailable}</div>
        ) : (
          <video
            className="max-h-full max-w-full"
            src={platform.files.toUrl(src.path)}
            controls
            playsInline
            onError={() => setVideoError(true)}
          />
        )}
      </div>
      <div className="flex flex-col gap-4">
        <div>
          <div className="truncate text-lg font-semibold" title={src.path}>
            {fileNameOf(src.path)}
          </div>
          <dl className="mt-2 space-y-1 text-base text-muted">
            <dd>
              {T.duration(src.durationSec)} / {T.size(src.width, src.height)}
            </dd>
            <dd>
              {src.fps ? `${T.fps(src.fps)} / ` : ""}
              {project.meta
                ? project.meta.hasAudio
                  ? T.audio.yes
                  : T.audio.no
                : src.hasAudio
                  ? T.audio.yes
                  : T.audio.unknown}
            </dd>
          </dl>
        </div>
        <div className="rounded border border-border bg-panel-2 p-3">
          <div className="text-sm text-muted">{T.sendTo}</div>
          {profile ? (
            <div className="mt-0.5 font-medium">
              {profile.name}
              {gpu ? `（${gpu}）` : ""}
            </div>
          ) : (
            <div className="mt-0.5 text-faint">{ja.status.noProfile}</div>
          )}
          {profile && <div className="mt-1 text-xs text-faint">{T.sendNotice(profile.name)}</div>}
        </div>
        {(!profile || status !== "connected") && (
          <ErrorNotice tone="warning" title={T.noConnection}>
            <Button size="sm" onClick={onOpenConnection}>
              {ja.home.setupConnection}
            </Button>
          </ErrorNotice>
        )}
        {resumable && <ErrorNotice tone="info" title={T.resumeTitle} body={T.resumeBody} />}
        <div className="mt-auto flex justify-end gap-2">
          <Button onClick={onCancel}>{ja.common.cancel}</Button>
          <Button
            variant="primary"
            disabled={!canStart}
            onClick={begin}
            icon={running ? <Spinner /> : <Play size={14} />}
          >
            {resumable ? T.resume : T.start}
          </Button>
        </div>
      </div>
    </div>
  );
}
