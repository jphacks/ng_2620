// 設定 > 一般：重み（約 7GB）の取得（#33）。HF のアクセス許諾の案内、トークン入力、進捗、中断、再開

import { ExternalLink } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { WeightFile, WeightsJob, WeightsStatus } from "@/domain/weights";
import { isWeightsRunning, weightsFraction } from "@/domain/weights";
import { errorText } from "@/presentation/shared/errors";
import { formatBytes } from "@/presentation/shared/format";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { Badge, Button, ErrorNotice, Label, ProgressBar, Spinner, TextInput } from "@/presentation/shared/ui";

const T = ja.weights;
const HF_CODES = ["HF_TOKEN_REQUIRED", "HF_TOKEN_INVALID", "HF_ACCESS_DENIED"];

const fileTone = (f: WeightFile) =>
  ({ ok: "success", unverified: "warning", partial: "warning", missing: "muted" })[f.state] as
    "success" | "warning" | "muted";

export function WeightsSection() {
  const { platform } = useServices();
  const weights = platform.weights;
  const [status, setStatus] = useState<WeightsStatus | null>(null);
  const [job, setJob] = useState<WeightsJob | null>(null);
  const [token, setToken] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!weights) return null;
    const s = await weights.status();
    setStatus(s);
    setJob(s.job);
    return s;
  }, [weights]);

  const watch = useCallback(async () => {
    if (!weights) return;
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await weights.watch(setJob, ctrl.signal);
      if (!ctrl.signal.aborted) await refresh();
    } catch (e) {
      if (!ctrl.signal.aborted) setFailure(errorText(e).title);
    }
  }, [weights, refresh]);

  useEffect(() => {
    if (!weights) return;
    void refresh()
      .then((s) => (s && isWeightsRunning(s.job) ? watch() : undefined))
      .catch((e) => setFailure(errorText(e).title));
    return () => abort.current?.abort();
  }, [weights, refresh, watch]);

  if (!weights) return null;

  const running = !!job && isWeightsRunning(job);
  const start = async () => {
    setFailure(null);
    try {
      await weights.start(token);
      setToken(""); // 入力欄からも消す。画面にもどこにも残さない
      void watch();
    } catch (e) {
      const t = errorText(e);
      setFailure(t.body ? `${t.title}：${t.body}` : t.title);
    }
  };
  const cancel = () => void weights.cancel().catch((e) => setFailure(errorText(e).title));

  const err = job?.state === "failed" ? job.error : null;
  const hfProblem = !!err && HF_CODES.includes(err.code);
  const fraction = job ? weightsFraction(job) : null;
  const anyPartial = status?.files.some((f) => f.state === "partial") ?? false;
  const startLabel = anyPartial || job?.state === "canceled" || job?.state === "failed" ? T.resume : T.start;

  return (
    <section data-testid="weights-section">
      <h3 className="mb-3 font-semibold">{T.title}</h3>
      {!status ? (
        <Spinner />
      ) : (
        <div className="space-y-3 text-sm">
          <p className="text-muted">{T.intro}</p>
          <dl className="grid grid-cols-[8rem_1fr] gap-y-1">
            <dt className="text-muted">{T.location}</dt>
            <dd className="truncate font-mono">{status.root}</dd>
            <dt className="text-muted">{T.free}</dt>
            <dd>{formatBytes(status.freeBytes)}</dd>
          </dl>

          {status.allReady ? (
            <div className="flex items-center gap-2">
              <Badge tone="success">{T.ready}</Badge>
            </div>
          ) : (
            <>
              <ul className="space-y-1">
                {status.files.map((f) => (
                  <li key={f.dest} className="flex items-center gap-2">
                    <Badge tone={fileTone(f)}>{T.fileState[f.state]}</Badge>
                    <span className="min-w-0 flex-1 truncate font-mono">{f.dest}</span>
                    {f.state === "partial" && <span className="text-faint">{formatBytes(f.bytes)}</span>}
                  </li>
                ))}
              </ul>

              {status.needsToken && !running && (
                <div className="space-y-1.5">
                  <p className="text-muted">{T.gatedGuide}</p>
                  <ul className="list-inside list-disc text-muted">
                    {[...new Set(status.files.filter((f) => f.gated && f.licenseUrl).map((f) => f.licenseUrl!))].map(
                      (u) => (
                        <li key={u}>
                          <a
                            href={u}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-accent underline"
                          >
                            {u.replace(/^https?:\/\//, "")} <ExternalLink size={12} />
                          </a>
                        </li>
                      ),
                    )}
                  </ul>
                  <Label htmlFor="hf-token">{T.tokenLabel}</Label>
                  <TextInput
                    id="hf-token"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="hf_…"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                  />
                  <p className="text-faint">{T.tokenNote}</p>
                </div>
              )}

              {running && job && (
                <div className="space-y-1" aria-live="polite">
                  <ProgressBar value={fraction ?? 0} indeterminate={fraction === null} />
                  <div className="text-muted">
                    {T.phase[job.phase ?? "preparing"] ?? job.phase}
                    {job.file ? `：${job.file}` : ""}
                    {job.bytesTotal ? `（${formatBytes(job.bytesDone ?? 0)} / ${formatBytes(job.bytesTotal)}）` : ""}
                    {job.fileCount ? ` ${Math.min((job.fileIndex ?? 0) + 1, job.fileCount)}/${job.fileCount}` : ""}
                  </div>
                </div>
              )}

              {err && (
                <ErrorNotice title={T.errors[err.code as keyof typeof T.errors] ?? T.errors.other} body={err.message}>
                  {hfProblem && err.detail?.url && (
                    <Button size="sm" onClick={() => void platform.shell.openUrl(err.detail!.url!)}>
                      {T.openLicense}
                    </Button>
                  )}
                </ErrorNotice>
              )}
              {job?.state === "canceled" && <p className="text-muted">{T.canceled}</p>}

              <div className="flex gap-2">
                {running ? (
                  <Button onClick={cancel}>{T.cancel}</Button>
                ) : (
                  <Button variant="primary" onClick={() => void start()}>
                    {startLabel}
                  </Button>
                )}
              </div>
            </>
          )}
          {job?.unpinned && job.unpinned.length > 0 && (
            <details className="text-faint">
              <summary>{T.unpinned}</summary>
              <ul className="font-mono text-xs">
                {job.unpinned.map((u) => (
                  <li key={u.dest}>
                    {u.dest} {u.sha256}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {failure && <ErrorNotice title={failure} />}
        </div>
      )}
    </section>
  );
}
