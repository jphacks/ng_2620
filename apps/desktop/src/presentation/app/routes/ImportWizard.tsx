// S2 取り込みウィザード：1 動画 → 2 解析 → 3 キャスト → 4 描画。完了したステップは ✓ で戻れる（解析は再実行しない）。
// feature の部品（import / jobs / cast / avatar-library）はここで組み合わせる（feature 間で import しないため）

import { Check } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { isStageDone } from "@/domain/job";
import { AvatarPicker, LicenseSummary, useImportAvatars } from "@/presentation/features/avatar-library";
import { CastStep } from "@/presentation/features/cast";
import type { CastSlots } from "@/presentation/features/cast";
import { UploadProgress, useImportStore, VideoStep } from "@/presentation/features/import";
import { JobStageList, LogDialog, ProgressivePreview } from "@/presentation/features/jobs";
import { jobErrorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useAvatars } from "@/presentation/shared/queries";
import { useJobsStore, useProjectJob } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, cx, ErrorNotice } from "@/presentation/shared/ui";

type Step = "video" | "analysis" | "cast" | "render";
const STEPS: Step[] = ["video", "analysis", "cast", "render"];

function LicenseFor({ avatarId }: { avatarId: string }) {
  const avatars = useAvatars();
  const a = avatars.data?.find((x) => x.avatarId === avatarId);
  return a ? <LicenseSummary avatar={a} /> : null;
}

export function ImportWizard() {
  const navigate = useNavigate();
  const project = useProjectStore((s) => s.current?.project);
  const importing = useImportStore((s) => (project ? s.runs[project.id]?.status === "running" : false));
  const analyze = useProjectJob("analyze");
  const render = useProjectJob("render");
  const { importPaths } = useImportAvatars();

  const initial = (): Step => {
    if (!project) return "video";
    if (project.jobs.render) return "render";
    if (project.jobs.analyze?.status === "succeeded") return "cast";
    if (project.jobs.analyze || importing || project.compute.mediaId) return "analysis";
    return "video";
  };
  const [step, setStep] = useState<Step>(initial);

  // 描画が（この画面を開いている間に）終わったらエディタへ
  const prevRender = useRef(render?.status);
  useEffect(() => {
    if (project && prevRender.current && prevRender.current !== "succeeded" && render?.status === "succeeded") navigate(`/project/${project.id}/edit`);
    prevRender.current = render?.status;
  }, [render?.status, project, navigate]);

  if (!project) return null;

  const done: Record<Step, boolean> = {
    video: !!project.compute.mediaId || !!project.jobs.analyze,
    analysis: project.jobs.analyze?.status === "succeeded",
    cast: !!project.jobs.render,
    render: project.jobs.render?.status === "succeeded",
  };
  const reachable = (s: Step) => s === step || done[s] || (s === "cast" && (isStageDone(analyze, "track") || done.analysis)) || (s === "analysis" && done.video);

  const slots: CastSlots = {
    renderAvatarPicker: ({ value, onChange }) => <AvatarPicker value={value} onChange={onChange} />,
    renderLicense: (avatarId) => <LicenseFor avatarId={avatarId} />,
    renderPreview: (highlight) => <ProgressivePreview highlightPersonId={highlight} prefer="masks" className="min-h-0 flex-1" />,
    importVrm: async (path) => (await importPaths([path]))[0]?.avatarId ?? null,
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ol className="flex shrink-0 items-center justify-center gap-2 border-b border-border bg-panel py-2">
        {STEPS.map((s, i) => (
          <li key={s} className="flex items-center gap-2">
            {i > 0 && <span className="h-px w-8 bg-border" />}
            <button
              type="button"
              disabled={!reachable(s)}
              onClick={() => setStep(s)}
              className={cx("flex items-center gap-1.5 rounded px-2.5 py-1 disabled:cursor-default", s === step ? "bg-raised text-fg" : "text-muted hover:text-fg disabled:text-faint")}
            >
              <span className={cx("flex h-5 w-5 items-center justify-center rounded-full text-xs", done[s] ? "bg-success text-white" : s === step ? "bg-accent text-accent-fg" : "bg-raised")}>
                {done[s] ? <Check size={12} strokeWidth={3} /> : i + 1}
              </span>
              {ja.wizard.steps[s]}
            </button>
          </li>
        ))}
      </ol>
      <div className="min-h-0 flex-1">
        {step === "video" && <VideoStep onCancel={() => navigate("/")} onStarted={() => setStep("analysis")} onOpenConnection={() => navigate("/settings/connection")} />}
        {step === "analysis" && <AnalysisStep onNext={() => setStep("cast")} onBackground={() => navigate("/")} onPickOtherVideo={() => navigate("/")} />}
        {step === "cast" && <CastStep slots={slots} onBack={() => setStep("analysis")} onRenderStarted={() => setStep("render")} onPickOtherVideo={() => navigate("/")} />}
        {step === "render" && <RenderStep onOpenEditor={() => navigate(`/project/${project.id}/edit`)} onBack={() => setStep("cast")} />}
      </div>
    </div>
  );
}

/** S2-2 解析：アップロード → ① 準備 ② 人物の追跡 ③ 3D 姿勢 ④ 前後関係、届いた結果のプレビュー */
function AnalysisStep({ onNext, onBackground, onPickOtherVideo }: { onNext(): void; onBackground(): void; onPickOtherVideo(): void }) {
  const navigate = useNavigate();
  const analyze = useProjectJob("analyze");
  const retry = useJobsStore((s) => s.retry);
  const [logOpen, setLogOpen] = useState(false);
  const trackDone = isStageDone(analyze, "track");
  const failed = analyze?.status === "failed" ? jobErrorText(analyze.error) : null;
  const mediaProblem = failed && (failed.code === "NO_PERSON_FOUND" || failed.code.startsWith("MEDIA_"));

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_minmax(280px,42%)] gap-6 p-6">
      <div className="flex min-h-0 flex-col gap-3">
        <UploadProgress onOpenConnection={() => navigate("/settings/connection")} />
        {analyze && <JobStageList job={analyze} />}
        {failed && analyze && (
          <ErrorNotice title={failed.title} body={failed.body}>
            {mediaProblem ? (
              <Button size="sm" onClick={onPickOtherVideo}>
                {ja.errorActions.pickOtherVideo}
              </Button>
            ) : (
              <Button size="sm" onClick={() => void retry(analyze.jobId)}>
                {ja.errorActions.retry}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={() => setLogOpen(true)}>
              {ja.errorActions.showLog}
            </Button>
          </ErrorNotice>
        )}
        <div className="mt-auto flex justify-end gap-2">
          <Button onClick={onBackground}>{ja.analysis.background}</Button>
          <Button variant="primary" disabled={!trackDone} onClick={onNext}>
            {ja.analysis.pickPersons} ▶
          </Button>
        </div>
        {analyze && <LogDialog open={logOpen} onOpenChange={setLogOpen} job={analyze} />}
      </div>
      <ProgressivePreview className="min-h-0" />
    </div>
  );
}

/** S2-4 描画：解析の残りと描画の進捗、メッシュのプレビュー。完了すると自動でエディタへ */
function RenderStep({ onOpenEditor, onBack }: { onOpenEditor(): void; onBack(): void }) {
  const analyze = useProjectJob("analyze");
  const render = useProjectJob("render");
  const retry = useJobsStore((s) => s.retry);
  const failed = render?.status === "failed" ? jobErrorText(render.error) : null;
  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_minmax(280px,42%)] gap-6 p-6">
      <div className="flex min-h-0 flex-col gap-4">
        <div className="font-medium">{ja.render.title}</div>
        {analyze && analyze.status !== "succeeded" && (
          <div>
            <div className="mb-1 text-sm text-muted">{ja.render.analyzeRest}</div>
            <JobStageList job={analyze} only={["mesh", "occlusion"]} startIndex={2} />
          </div>
        )}
        {render && <JobStageList job={render} />}
        {failed && render && (
          <ErrorNotice title={failed.title} body={failed.body}>
            <Button size="sm" onClick={() => void retry(render.jobId)}>
              {ja.errorActions.retry}
            </Button>
          </ErrorNotice>
        )}
        <div className="mt-auto flex justify-end gap-2">
          <Button onClick={onBack}>{ja.common.back}</Button>
          <Button variant="primary" disabled={render?.status !== "succeeded"} onClick={onOpenEditor}>
            {ja.render.openEditor} ▶
          </Button>
        </div>
      </div>
      <ProgressivePreview prefer="mesh" className="min-h-0" />
    </div>
  );
}
