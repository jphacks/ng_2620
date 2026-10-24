# F08 avatar-render — アバター描画ジョブとレイヤー生成（removal → avatar → layers）

| 項目 | 内容 |
|---|---|
| レーン | C |
| 優先度 | P0（VRM の差し替えで描画だけ再実行は P1 の FR-21、仕上げパラメータは FR-22） |
| 関連 | FR-12 / [ADR-0004](../../adr/0004-job-stages-and-progressive-results.md) / [ADR-0005](../../adr/0005-preview-composite-client-export-server.md) / [contracts §4.2](../../architecture/contracts.md#42-layers-zip-の中身) / [pipeline-integration §3.3–3.5](../../architecture/pipeline-integration.md#33-キャスト置換対象と-vrm) |
| 依存 | F04（analyze の成果物と `fusion_state.pkl`）、F13 |

## ユーザーストーリー
- ユーザーとして、選んだ人物が選んだアバターに置き換わったプレビューを、アプリでなめらかに再生・編集できるようにしたい。

## 受け入れ条件
- [ ] `render` ジョブ（`{analyzeJobId, cast, finish}`）が analyze の成功後に開始する（未完了なら `queued` のまま待つ。analyze が失敗なら `DEPENDENCY_NOT_READY` で失敗）。
- [ ] `cast` に含まれる人物だけを除去・置換し、人物ごとに指定の VRM を使う。含まれない人物は実写のまま残り、その人物がアバターの手前にいる画素はアバターが隠れる。
- [ ] 成果物：`layers`（`clean/`・`avatar/<pid>/`・`hide/<pid>/`）、`layers_index`（`order`、`avatarIds`）、`preview_video`。すべてプロキシ解像度。
- [ ] 10 秒（300 フレーム）で 2 分以内（fusion の再計算なし。L4 / RTX 級）。
- [ ] `preview_video` と、アプリの合成規則（`domain/composite`）で `layers` から作った画像の差が 0.5% 未満（[contracts §5](../../architecture/contracts.md#5-合成規則composite-rule)）。
- [ ] VRM が読めない場合は `AVATAR_INVALID` で失敗し、どの人物の VRM かを `detail` に入れる。

## 設計（サーバー）
- `RenderJob = [RemovalAvatarLayersStage]`：`avatar_app.py` 1 プロセスで除去・描画・レイヤー書き出しを行い、ログ行でステージ境界（`removal` / `avatar` / `layers`）を報告する。
  ```
  python pipeline/scripts/avatar_app.py --pipeline_out <pout> --out <work>/render --name <projectId>
      --gpu_removal --fusion_state <occ>/fusion_state.pkl
      --targets 1,3 --vrm_map 1=<avatars>/av_x.vrm,3=<avatars>/av_y.vrm
      --smooth 0.05 [--no_foot_lock] [--no_spring]
      --export_layers <work>/render/layers
  ```
- VRM の保存：`VVLOG_DATA_ROOT/avatars/<avatarId>.vrm`（`POST /v1/avatars` で登録済み）。
- `layers` の zip 化は store（無圧縮）。WebP のエンコードは `Sink` のワーカースレッドで（描画を止めない）。

## パイプライン改修
- §3.3 `--targets` / `--vrm_map`、置換しない人物の手前判定（`people=True` を非ターゲットに限定）
- §3.4 `--fusion_state`
- §3.5 `--export_layers`

## タスク分解
1. §3.5 `--export_layers`（最初に。フィクスチャ v1 に必要）＋手動確認（フレーム 0 / 中央 / 最後の各レイヤーを目視）。
2. §3.4 `--fusion_state`（`occlusion` で保存、ここで読み込み）。出力の hide が再計算時と同一であることを確認（`np.array_equal`）。
3. §3.3 `--targets` / `--vrm_map` と非ターゲットの手前判定。2 人映る素材で確認。
4. `RenderJob` のステージ実装（進捗の解釈：`[avatar]` 行、描画ループのフレーム番号を出す 1 行を `avatar_app.py` に追加）。
5. 合成一致テスト（`packages/contracts/tests/composite/`）の Python 側。

## テスト
- `FakePipelineRunner` でステージの状態遷移と成果物公開。
- 実 GPU：10 秒サンプルで `preview_video` を目視、時間計測。

## P1 / スコープ外
- `render` だけの再実行（VRM 差し替え・仕上げパラメータ変更）は API 上はそのまま可能（新しい `render` ジョブ）。アプリの UI は P1。
- 人物ごとの深度レイヤー（プレビューの z バッファ）は P2。
