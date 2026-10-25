// 設定 > 接続（S6、F03）：プロファイルの追加・編集・削除・既定の選択、接続テスト。「この PC」はフルモードで自動登録される

import { CheckCircle2, Plus, Server, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import type { TestResult } from "@/application/usecases";
import type { ComputeProfile, ProfileKind } from "@/domain/connection";
import { isInsecureRemote, LOCAL_PROFILE_ID, normalizeBaseUrl, validateProfile } from "@/domain/connection";
import { errorText } from "@/presentation/shared/errors";
import { formatVram } from "@/presentation/shared/format";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import {
  Badge,
  Button,
  ConfirmDialog,
  cx,
  EmptyState,
  ErrorNotice,
  Label,
  Select,
  Spinner,
  StatusDot,
  TextInput,
} from "@/presentation/shared/ui";

const T = ja.connection;

function newProfile(id: string): ComputeProfile {
  return { id, name: "", kind: "remote", baseUrl: "https://", tokenRef: id };
}

export function ConnectionSettings() {
  const { platform } = useServices();
  const profiles = useConnectionStore((s) => s.profiles);
  const activeProfileId = useConnectionStore((s) => s.activeProfileId);
  const status = useConnectionStore((s) => s.state.status);
  const [editing, setEditing] = useState<{ profile: ComputeProfile; isNew: boolean } | null>(null);

  const edit = (next: { profile: ComputeProfile; isNew: boolean } | null) => setEditing(next);

  const startNew = () => {
    const id = `prof_${platform.clock.now().getTime().toString(36)}`;
    edit({ profile: newProfile(id), isNew: true });
  };

  return (
    <div className="flex h-full min-h-0">
      <div className="flex w-72 shrink-0 flex-col border-r border-border">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="font-semibold">{T.profiles}</div>
          <Button size="sm" icon={<Plus size={14} />} onClick={startNew}>
            {ja.common.add}
          </Button>
        </div>
        <ul className="min-h-0 flex-1 overflow-auto px-2 pb-2">
          {profiles.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => edit({ profile: p, isNew: false })}
                className={cx(
                  "flex w-full items-center gap-2 rounded px-2 py-2 text-left hover:bg-raised",
                  editing?.profile.id === p.id && "bg-raised",
                )}
              >
                <Server size={14} className="shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate">{p.name}</span>
                    {p.id === activeProfileId && (
                      <StatusDot
                        tone={status === "connected" ? "success" : status === "reconnecting" ? "warning" : "muted"}
                      />
                    )}
                  </div>
                  <div className="truncate text-xs text-faint">{p.baseUrl}</div>
                </div>
                {p.id === activeProfileId && <Badge tone="accent">{T.isDefault}</Badge>}
                {p.devOnly && <Badge>{T.devOnly}</Badge>}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="min-w-0 flex-1 overflow-auto p-6">
        {editing ? (
          <ProfileEditor
            key={editing.profile.id}
            initial={editing.profile}
            isNew={editing.isNew}
            onDone={() => setEditing(null)}
          />
        ) : profiles.length === 0 ? (
          <EmptyState icon={<Server size={32} />} title={T.profiles} body={T.empty}>
            <Button variant="primary" icon={<Plus size={14} />} onClick={startNew}>
              {T.add}
            </Button>
          </EmptyState>
        ) : (
          <div className="text-muted">{T.selectHint}</div>
        )}
      </div>
    </div>
  );
}

function ProfileEditor({ initial, isNew, onDone }: { initial: ComputeProfile; isNew: boolean; onDone(): void }) {
  const { uc } = useServices();
  const save = useConnectionStore((s) => s.save);
  const remove = useConnectionStore((s) => s.remove);
  const select = useConnectionStore((s) => s.select);
  const activeProfileId = useConnectionStore((s) => s.activeProfileId);
  const [p, setP] = useState(initial);
  const [token, setToken] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [touched, setTouched] = useState(false);

  const problems = validateProfile(p);
  const insecure = isInsecureRemote(p);
  const isActive = activeProfileId === p.id;
  // 「この PC」はフルモードで毎回自動登録される：宛先とトークンは編集できず、消しても次の起動で戻る
  const auto = p.id === LOCAL_PROFILE_ID;

  const onTest = async () => {
    setTesting(true);
    setResult(null);
    try {
      const candidate = { ...p, baseUrl: normalizeBaseUrl(p.baseUrl) };
      setResult(await uc.testConnection.execute(candidate, token ? { token } : {}));
    } finally {
      setTesting(false);
    }
  };

  const onSave = async () => {
    setTouched(true);
    if (problems.length) return;
    await save(p, token || (isNew ? "" : undefined));
    setToken("");
    if (isNew) onDone();
  };

  return (
    <div className="max-w-xl space-y-4">
      <div className="text-lg font-semibold">{isNew ? T.newProfile : p.name}</div>
      <div className="grid grid-cols-[8rem_1fr] items-center gap-x-3 gap-y-3">
        <Label htmlFor="pf-name">{T.name}</Label>
        <TextInput
          id="pf-name"
          value={p.name}
          onChange={(e) => setP({ ...p, name: e.target.value })}
          placeholder="remote-win"
          disabled={auto}
        />
        <Label>{T.kind}</Label>
        <Select<ProfileKind>
          aria-label={T.kind}
          value={p.kind}
          onChange={(kind) => !auto && setP({ ...p, kind })}
          options={[
            { value: "local", label: T.kinds.local },
            { value: "remote", label: T.kinds.remote },
          ]}
        />
        <Label htmlFor="pf-url">{T.url}</Label>
        <TextInput
          id="pf-url"
          value={p.baseUrl}
          onChange={(e) => setP({ ...p, baseUrl: e.target.value })}
          placeholder={T.urlHint[p.kind]}
          spellCheck={false}
          disabled={auto}
        />
        {!auto && (
          <>
            <Label htmlFor="pf-token">{T.token}</Label>
            <TextInput
              id="pf-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder={isNew ? "" : T.tokenKeep}
              autoComplete="off"
              spellCheck={false}
            />
          </>
        )}
      </div>
      {auto && <div className="text-sm text-muted">{T.local.autoNote}</div>}
      {!auto && <div className="text-xs text-faint">{T.storageNote}</div>}

      {touched && problems.length > 0 && <ErrorNotice title={problems.map((x) => T.problems[x]).join(" / ")} />}
      {insecure && <ErrorNotice tone="warning" title={T.insecure} />}

      <div className="flex flex-wrap items-center gap-2">
        {!auto && (
          <Button variant="primary" onClick={() => void onSave()}>
            {ja.common.save}
          </Button>
        )}
        <Button
          onClick={() => void onTest()}
          disabled={testing || problems.length > 0}
          icon={testing ? <Spinner /> : undefined}
        >
          {testing ? T.testing : T.test}
        </Button>
        {!isNew && !isActive && <Button onClick={() => void select(p.id)}>{T.setDefault}</Button>}
        {!isNew && !auto && (
          <Button
            variant="ghost"
            className="ml-auto text-danger"
            icon={<Trash2 size={14} />}
            onClick={() => setConfirmRemove(true)}
          >
            {ja.common.remove}
          </Button>
        )}
      </div>

      {result && <TestResultView result={result} />}

      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title={T.removeTitle(p.name)}
        confirmLabel={ja.common.remove}
        danger
        onConfirm={() => {
          void remove(p.id);
          onDone();
        }}
      />
    </div>
  );
}

export function TestResultView({ result }: { result: TestResult }) {
  if (result.ok) {
    const c = result.capabilities;
    return (
      <div className="rounded border border-success/40 bg-success/10 p-3">
        <div className="flex items-center gap-2 font-medium">
          <CheckCircle2 size={16} className="text-success" />
          {T.testOk}
        </div>
        <dl className="mt-2 grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
          <dt className="text-muted">{T.gpu}</dt>
          <dd>{c.gpuName ?? ja.common.unknown}</dd>
          <dt className="text-muted">{T.vram}</dt>
          <dd>{formatVram(c.vramMb)}</dd>
          <dt className="text-muted">{T.queueLength}</dt>
          <dd>{c.queueLength}</dd>
          <dt className="text-muted">{T.apiVersion}</dt>
          <dd>{result.health.apiVersion}</dd>
          <dt className="text-muted">{T.serverVersion}</dt>
          <dd>{result.health.version}</dd>
        </dl>
      </div>
    );
  }
  const e = errorText(result.error);
  return (
    <div className="rounded border border-danger/40 bg-danger/10 p-3">
      <div className="flex items-center gap-2 font-medium">
        <XCircle size={16} className="text-danger" />
        {T.testFailed[result.failedStep]}
      </div>
      <div className="mt-1 text-sm text-muted">
        {result.failedStep === "auth"
          ? e.body
          : result.failedStep === "reach" && result.reason
            ? result.reason === "cors"
              ? T.reachReason.cors(result.pageOrigin ?? window.location.origin)
              : T.reachReason[result.reason]
            : result.error.message}
      </div>
    </div>
  );
}
