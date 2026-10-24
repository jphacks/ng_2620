# F04 analysis-pipeline — 解析ジョブ（ingest → track → mesh → occlusion）

| 項目 | 内容 |
|---|---|
| レーン | C |
| 優先度 | P0 |
| 関連 | FR-06, FR-08 / [ADR-0004](../../adr/0004-job-stages-and-progressive-results.md) / [ADR-0011](../../adr/0011-input-limits-and-resolutions.md) / [pipeline-integration](../../architecture/pipeline-integration.md) |
| 依存 | F13（ジョブ基盤）、パイプラインの移植 |

## ユーザーストーリー
- ユーザーとして、動画をアップロードしたら、人物の追跡・3D 姿勢・前後関係の推定が自動で進み、終わった段階から結果を見たい。
- 人物の一覧はできるだけ早く欲しい（その間にアバターを選びたい）。

## 受け入れ条件
- [ ] `POST /v1/projects/{id}/jobs {type:"analyze", params:{mediaId, maxTargets}}` でキューに入り、順に 4 ステージが走る。
- [ ] `ingest`：回転適用、CFR（30fps 以下）、制約検証（違反は `MEDIA_*` で失敗）、`meta`・`proxy_frames` を公開。フル解像度フレームと音声をサーバー内に保存。
- [ ] `track`：追跡完了時点で `persons`・`person_thumbs`・`track_masks` を公開（**メッシュの完了を待たない**）。人物 0 人なら `NO_PERSON_FOUND` で失敗。
- [ ] `mesh`：`mesh_preview` を公開。
- [ ] `occlusion`：`units`・`units_index` を公開し、`fusion_state.pkl` を保存（後段で再利用）。
- [ ] 各ステージで `progress` を少なくとも 2 秒に 1 回送る（フレーム数ベース。取れない区間は段階内の経過時間から推定）。
- [ ] 10 秒・1080x1920 の動画で、L4 / RTX 5060 Ti 級の GPU で全体 5 分以内、`persons` 公開まで 2 分以内。
- [ ] 取り消しでサブプロセスが終了し、GPU メモリが解放される（`nvidia-smi` で確認）。

## 設計（サーバー）
- application/stages/：ステージを `Stage` インターフェースで表す。
  ```python
  class Stage(Protocol):
      name: StageName
      def inputs_ready(self, ctx: JobContext) -> bool: ...
      async def run(self, ctx: JobContext, runner: PipelineRunnerPort, store: ArtifactStorePort,
                    emit: Callable[[JobEvent], None]) -> None: ...
  ```
  `AnalyzeJob = [IngestStage, TrackMeshStage(track と mesh を 1 プロセスで実行し、ログ行で境界を検出), OcclusionStage]`。
- `PipelineRunnerPort.run(cmd, env, cwd, on_line) -> exit_code`：`SubprocessPipelineRunner` が `pipeline/` のスクリプトを起動。標準出力を 1 行ずつ `on_line` へ。
- 進捗の解釈（`adapters/progress_parsers.py`）：
  - `track`：tqdm の `n/total`（SAM3 propagate）。`[STAGE][sam3_propagate]` で終了。
  - `mesh`：tqdm（HMR のバッチ。`--hmr_stride` ではキーフレーム→間のフレームの順で、総数はフレーム数と一致しない）。`[STAGE][hmr_4d]` で終了。
  - `occlusion`：`<name> <frame> pts ... kept ...` の frame 番号 / 総数、`[fusion_v2] ... total` で終了。
- 作業ディレクトリ：`VVLOG_DATA_ROOT/projects/<projectId>/work/<jobId>/{full,proxy,pout,occ}`。成果物は `.../artifacts/<jobId>/`。
- 環境：`VVLOG_CKPT_ROOT` を `configs/body4d.yaml` の `ckpt_root` に反映（起動時に一時 config を生成し `--config` で渡す、または環境変数 `BODY4D_CKPT_ROOT`）、`FUSION_DEPTH`、`--tracker_size`、`USE_TF=0` など（[pipeline-integration §3.7](../../architecture/pipeline-integration.md#37-依存とピンcolab--linux-で確認済みの組み合わせ)）。

## パイプライン改修（[pipeline-integration §3](../../architecture/pipeline-integration.md#3-必要な改修詳細)）
- §3.1 追跡完了時点の人物一覧（`--emit_persons`）
- §3.2 遮蔽物ユニット（連結、ラベル画像、`units.json`）
- §3.4 `fusion_state.pkl` の保存
- `max_targets` の上書き

## タスク分解
1. `ingest`（ffprobe / ffmpeg：回転、CFR、フル・プロキシ書き出し、音声分離、`meta.json`）＋テスト（縦動画・HEVC・VFR のサンプル）。
2. `TrackMeshStage`（`offline_app_lite.py` 呼び出し、進捗の解釈、`mesh_preview` の公開）。
3. §3.1 `--emit_persons`（パイプライン側）と `persons` 系の公開。
4. `OcclusionStage`（`run_fusion_v2.py` 呼び出し、進捗）、§3.4 状態保存。
5. §3.2 ユニット連結（`pipeline/scripts/occlusion/units.py` を新設：入力 `ev`, `dec`, `cands.npz` → `units/`, `units.json`）。単体テスト：人工の 2 トラック（終端と始端の IoU 0.8）が 1 ユニットになる。
6. GPU 機で 10 秒サンプルを通し、時間を記録（`stats.json` を成果物に含める）。

## テスト
- ステージの単体テストは `FakePipelineRunner`（記録済みのログ行を流し、出力ファイルを fixtures からコピー）で GPU 無しに行う。
- 実 GPU での通し：`services/gpu_server/scripts/smoke_analyze.py <video>`。

## P1 / スコープ外
- 途中から入る人物の追跡（P2）、30 秒超のチャンク処理（スコープ外）、`mesh` と `occlusion` の GPU 並走（P2）。
