// 初回セットアップ（解析環境 → モデルの重み）を、ボタン 1 つで順に進める。未完了なら起動時にダイアログで案内し、
// 閉じたあとも済ませるまで上部に案内を残す（やり忘れ防止）。フルモードだけ。設定 > 一般の重み取得は、そのまま残す

import { AlertTriangle, ExternalLink } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { BootstrapJob, BootstrapStatus } from "@/domain/bootstrap";
import { bootstrapFraction, isBootstrapRunning } from "@/domain/bootstrap";
import type { WeightsJob, WeightsStatus } from "@/domain/weights";
import { weightsFraction } from "@/domain/weights";
import { errorText } from "@/presentation/shared/errors";
import { formatBytes } from "@/presentation/shared/format";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { Badge, Button, Dialog, ErrorNotice, Label, ProgressBar, TextInput } from "@/presentation/shared/ui";

const T = ja.setup;
const KEY = "vvlog.setupPrompt.seen";
const TOKEN_PAGE = "https://huggingface.co/settings/tokens";

function wasSeen(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

const needsEnv = (b: BootstrapStatus | null) => !!b && !b.ready;
const needsWeights = (w: WeightsStatus | null) => !!w && !w.allReady;
const unsupported = (b: BootstrapStatus | null) => !!b && !b.ready && b.judgement.verdict === "unsupported";

export function SetupPrompt() {
  const { platform } = useServices();
  const { bootstrap, weights } = platform;
  const [bs, setBs] = useState<BootstrapStatus | null>(null);
  const [ws, setWs] = useState<WeightsStatus | null>(null);
  const [bJob, setBJob] = useState<BootstrapJob | null>(null);
  const [wJob, setWJob] = useState<WeightsJob | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const [b, w] = await Promise.all([bootstrap?.status() ?? null, weights?.status() ?? null]);
    setBs(b);
    setWs(w);
    setBJob(b?.job ?? null);
    setWJob(w?.job ?? null);
    return { b, w };
  }, [bootstrap, weights]);

  useEffect(() => {
    if (!bootstrap && !weights) return;
    let alive = true;
    void refresh().then(
      ({ b, w }) => {
        if (alive && (needsEnv(b) || needsWeights(w)) && !wasSeen()) setOpen(true);
      },
      () => {
        // 状態が取れないときは案内しない（接続の問題は別の表示が扱う）
      },
    );
    return () => {
      alive = false;
      abort.current?.abort();
    };
  }, [bootstrap, weights, refresh]);

  const canRun = !!ws && needsWeights(ws) ? !ws.needsToken || token.trim() !== "" : true;

  /** 解析環境 → 重み の順に進める。どちらも途中からの再開に対応しているので、何度押しても続きから進む */
  const run = async () => {
    const ctrl = new AbortController();
    abort.current = ctrl;
    setBusy(true);
    setFailure(null);
    try {
      if (bootstrap && needsEnv(bs) && !unsupported(bs)) {
        try {
          await bootstrap.start();
        } catch (e) {
          if ((e as { code?: string }).code !== "BOOTSTRAP_BUSY") throw e;
        }
        const last = await bootstrap.watch(setBJob, ctrl.signal);
        if (last?.state !== "succeeded") return;
      }
      if (weights && needsWeights(ws)) {
        try {
          await weights.start(token);
          setToken(""); // 入力欄からも消す。画面にもどこにも残さない
        } catch (e) {
          if ((e as { code?: string }).code !== "WEIGHTS_BUSY") throw e;
        }
        await weights.watch(setWJob, ctrl.signal);
      }
    } catch (e) {
      if (!ctrl.signal.aborted) {
        const t = errorText(e);
        setFailure(t.body ? `${t.title}：${t.body}` : t.title);
      }
    } finally {
      setBusy(false);
      if (!ctrl.signal.aborted) await refresh().catch(() => undefined);
    }
  };

  const cancel = () => {
    const stop = bJob && isBootstrapRunning(bJob) ? bootstrap?.cancel() : weights?.cancel();
    void stop?.catch((e) => setFailure(errorText(e).title));
  };

  const close = (v: boolean) => {
    setOpen(v);
    if (v) return;
    try {
      sessionStorage.setItem(KEY, "1");
    } catch {
      // 記憶できなくても、今回だけ閉じればよい
    }
  };

  if (!bootstrap && !weights) return null;
  const pending = needsEnv(bs) || needsWeights(ws);
  if (!pending && !open) return null;

  const cant = unsupported(bs);
  const everStarted =
    [bJob?.state, wJob?.state].some((s) => s === "failed" || s === "canceled") ||
    !!ws?.files.some((f) => f.state === "partial");
  const bErr = bJob?.state === "failed" ? bJob.error : null;
  const wErr = wJob?.state === "failed" ? wJob.error : null;
  const bFrac = bs ? bootstrapFraction(bs.steps, bJob ?? { state: "idle" }) : null;
  const wFrac = wJob ? weightsFraction(wJob) : null;
  const gatedUrls = [...new Set((ws?.files ?? []).filter((f) => f.gated && f.licenseUrl).map((f) => f.licenseUrl!))];
  const showToken = needsWeights(ws) && ws?.needsToken && !busy && !cant;

  return (
    <>
      {pending && (
        <div role="alert" className="flex items-center gap-2.5 border-b border-border bg-panel-2 px-4 py-2 text-sm">
          <AlertTriangle size={16} className="shrink-0 text-warning" />
          <span className="min-w-0 flex-1">{T.banner}</span>
          <Button size="sm" variant="primary" onClick={() => setOpen(true)}>
            {T.open}
          </Button>
        </div>
      )}
      <Dialog open={open} onOpenChange={close} title={T.title} description={T.body} width="w-[640px]">
        <div className="space-y-4 text-sm" data-testid="setup-prompt">
          {bs && (
            <StepRow
              label={T.env}
              state={bs.ready ? "done" : bJob && isBootstrapRunning(bJob) ? "running" : "todo"}
              fraction={bFrac}
              detail={bJob && isBootstrapRunning(bJob) ? bs.steps.find((s) => s.id === bJob.step)?.label : undefined}
            />
          )}
          {ws && (
            <StepRow
              label={T.weights}
              state={ws.allReady ? "done" : wJob?.state === "running" ? "running" : "todo"}
              fraction={wFrac}
              detail={
                wJob?.state === "running" && wJob.bytesTotal
                  ? `${formatBytes(wJob.bytesDone ?? 0)} / ${formatBytes(wJob.bytesTotal)}`
                  : undefined
              }
            />
          )}

          {cant && (
            <ErrorNotice
              title={T.unsupportedTitle}
              body={`${T.unsupportedBody}（${bs?.judgement.reasons.join(", ")}）`}
            />
          )}

          {showToken && (
            <div className="space-y-1.5">
              <p className="text-muted">{T.tokenGuide}</p>
              <ul className="list-inside list-disc text-muted">
                {[...gatedUrls, TOKEN_PAGE].map((u) => (
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
                ))}
              </ul>
              <Label htmlFor="setup-hf-token">{T.tokenLabel}</Label>
              <TextInput
                id="setup-hf-token"
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

          {bErr && <ErrorNotice title={T.envFailed} body={[bErr.message, bErr.hint].filter(Boolean).join(" ")} />}
          {wErr && (
            <ErrorNotice
              title={ja.weights.errors[wErr.code as keyof typeof ja.weights.errors] ?? ja.weights.errors.other}
              body={wErr.message}
            />
          )}
          {failure && <ErrorNotice title={failure} />}
          {!pending && <p className="text-success">{T.allDone}</p>}

          <div className="flex justify-end gap-2">
            {pending &&
              !cant &&
              (busy ? (
                <Button onClick={cancel}>{T.cancel}</Button>
              ) : (
                <Button variant="primary" disabled={!canRun} onClick={() => void run()}>
                  {everStarted ? T.resume : T.start}
                </Button>
              ))}
            <Button onClick={() => close(false)}>{pending ? T.later : ja.common.close}</Button>
          </div>
          {pending && !cant && !canRun && <p className="text-right text-faint">{T.needToken}</p>}
        </div>
      </Dialog>
    </>
  );
}

function StepRow({
  label,
  state,
  fraction,
  detail,
}: {
  label: string;
  state: "done" | "running" | "todo";
  fraction: number | null;
  detail?: string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Badge tone={state === "done" ? "success" : state === "running" ? "warning" : "muted"}>
          {state === "done" ? T.done : state === "running" ? T.running : T.todo}
        </Badge>
        <span className="font-medium">{label}</span>
        {detail && <span className="min-w-0 truncate text-muted">{detail}</span>}
      </div>
      {state === "running" && <ProgressBar value={fraction ?? 0} indeterminate={fraction === null} />}
    </div>
  );
}
