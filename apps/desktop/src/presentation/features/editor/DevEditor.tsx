// /#/dev/editor?fixture=v0：合成フィクスチャ v0（fixtures/synth）をプロジェクトとして開き、エディタへ（レーン B の単独確認用）。
// Tauri の開発ビルドで使う（ブラウザだけのときはファイルを読めない）。
//   &check=1                 WebGL の合成を正解画像と比べ、結果を表示する（F09 の一致確認）
//   &scenario=unit2front     エディタを開いた後、フレーム 48・ユニット 2 を選択・人物 2 の前に・遮蔽物を表示、を流す

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { errorText } from "@/presentation/shared/errors";
import { useServices } from "@/presentation/shared/services";
import { useEditorStore } from "@/presentation/shared/stores/editorStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, EmptyState, Spinner } from "@/presentation/shared/ui";
import type { CheckResult } from "./selfCheck";
import { runCompositeCheck } from "./selfCheck";

let scenarioApplied = false;

function applyScenario(name: string | null) {
  if (name !== "unit2front" || scenarioApplied) return; // StrictMode で effect が 2 回走っても 1 回だけ
  scenarioApplied = true;
  const off = useEditorStore.subscribe((s) => {
    if (!s.data) return;
    off();
    const st = useEditorStore.getState();
    st.seek(48);
    st.select(2);
    st.selectPerson(2);
    st.setMode(2, 2, "front", [0, s.data.frameCount - 1]);
    if (!st.layers.units) st.toggleLayer("units");
  });
}

export function DevEditor() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { uc, editorMedia } = useServices();
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<CheckResult[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  const fixture = params.get("fixture") ?? "v0";
  const check = params.get("check") === "1";
  const scenario = params.get("scenario");

  useEffect(() => {
    if (fixture !== "v0") {
      setError(`不明なフィクスチャです：${fixture}`);
      return;
    }
    (async () => {
      const p = await useProjectStore.getState().openAt(__FIXTURE_V0__);
      setProjectId(p.id);
      if (!check) {
        applyScenario(scenario);
        navigate(`/project/${p.id}/edit`, { replace: true });
        return;
      }
      const data = await uc.loadEditorData.execute(p);
      const exp = (name: string) => `${__FIXTURE_V0__}/expected/${name}_00048.png`;
      setResults(
        await runCompositeCheck(data, editorMedia, 48, [
          { name: "手修正なし", expected: exp("none"), modes: {} },
          { name: "ユニット 2 を人物 2 の前に", expected: exp("unit2front"), modes: { "2:2": "front" } },
        ]),
      );
    })().catch((e: unknown) => setError(`${errorText(e).title}：${errorText(e).body}`));
  }, [fixture, check, scenario, navigate, uc, editorMedia]);

  if (error) return <EmptyState title="フィクスチャを開けません" body={`${error}（${__FIXTURE_V0__}）`} />;
  if (results) {
    return (
      <div className="mx-auto max-w-lg p-6 text-sm">
        <h2 className="mb-3 font-semibold">合成の一致確認（フレーム 48、|差| &gt; 24/255 の画素の割合、基準 0.5% 未満）</h2>
        <ul className="space-y-1 font-mono">
          {results.map((r) => (
            <li key={r.name} data-check={r.ok ? "ok" : "ng"}>
              {r.ok ? "OK" : "NG"} {r.name}：{(r.ratio * 100).toFixed(3)}%
            </li>
          ))}
        </ul>
        {projectId && (
          <Button className="mt-4" variant="primary" onClick={() => navigate(`/project/${projectId}/edit`)}>
            エディタを開く
          </Button>
        )}
      </div>
    );
  }
  return (
    <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
      <Spinner /> {__FIXTURE_V0__}
    </div>
  );
}
