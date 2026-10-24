# F13 gpu-server-runtime — サーバー基盤（ジョブ実行・成果物保存・認証・配備）

| 項目 | 内容 |
|---|---|
| レーン | C |
| 優先度 | P0 |
| 関連 | [ADR-0002](../../adr/0002-gpu-server-and-compute-backend-port.md) / [ADR-0012](../../adr/0012-transport-and-auth.md) / [ADR-0014](../../adr/0014-server-stack.md) / [architecture/overview §5–6](../../architecture/overview.md#5-gpu-サーバーの層とディレクトリ) / [contracts](../../architecture/contracts.md) |
| 依存 | 契約（M0） |

## ユーザーストーリー
- 開発者として、Windows の GPU 機でもクラウドの GPU VM でも、同じサーバーを 1 コマンドで起動したい。
- アプリとして、ジョブの状態がサーバー再起動や通信断に耐えてほしい。

## 受け入れ条件
- [ ] `contracts §2.1` の全エンドポイント（予約は 501）を実装。認証（Bearer）、エラー形式（§2.3）。
- [ ] ジョブキュー：GPU 1 枚につき 1 ワーカー、FIFO、依存（`dependsOn`）が満たされるまで待つ。取り消しで実行中のサブプロセスを終了（プロセスグループごと）。
- [ ] ジョブ状態を `VVLOG_DATA_ROOT/jobs/<jobId>/job.json` に保存。イベントを `events.jsonl` に追記し、SSE の `Last-Event-ID` 再開に使う。起動時に `running` を `failed(INTERRUPTED)` に。
- [ ] アップロード：断片の保存、受信状況、結合、sha256 照合。
- [ ] 成果物：`ArtifactStore` が sha256・サイズ・content-type を記録、`Range` 対応で配信。
- [ ] OpenAPI の契約テストが通る。
- [ ] 配備：
  - Windows（RTX）：`services/gpu_server/run_local.ps1`（venv、環境変数、uvicorn 起動）。
  - Linux GPU VM：`services/gpu_server/run_linux.sh`（同上）。Docker は P1。
  - リモート公開（今回のデモ）：Tailscale。Windows 機で `tailscale serve --https=443 http://127.0.0.1:8765`、デモ用 Mac を同じ tailnet に参加させて接続。手順を `services/gpu_server/DEPLOY.md` に（Funnel で外部公開はしない）。
  - 重み：`python weights/fetch.py --dest $VVLOG_CKPT_ROOT`（[ADR-0008](../../adr/0008-implementation-repo-and-model-placement.md)、ゲート付きは `HF_TOKEN`）。
- [ ] `GET /v1/capabilities` が GPU 名・VRAM（`torch.cuda` ではなく `nvidia-smi` で取得し、サーバー本体で torch を import しない）・入力上限・キュー長を返す。
- [ ] ログ：サーバーのログとジョブごとのログ（サブプロセスの標準出力）をファイルに。`GET /v1/jobs/{id}/logs`。

## 設計
- 層：[overview §5](../../architecture/overview.md#5-gpu-サーバーの層とディレクトリ)。
- domain：`Job`（状態機械、許される遷移以外は例外）、`Stage`、`Artifact`、`Upload`。
- application/ports：
  ```python
  class PipelineRunnerPort(Protocol):
      async def run(self, cmd: list[str], env: dict, cwd: Path, on_line: Callable[[str], None]) -> int: ...
      async def terminate(self) -> None: ...
  class ArtifactStorePort(Protocol):
      def publish(self, job_id: str, name: str, path: Path, kind: str, stage: str) -> Artifact: ...
      def list(self, job_id: str) -> list[Artifact]: ...
      def open(self, job_id: str, name: str) -> Path: ...
  class JobRepositoryPort(Protocol): save(job), get(job_id), list(project_id), list_unfinished()
  class EventBusPort(Protocol): publish(job_id, event) -> int; subscribe(job_id, after_id) -> AsyncIterator
  class JobQueuePort(Protocol): enqueue(job_id); position(job_id); cancel(job_id)
  class MediaPort(Protocol): probe(path), normalize(src, out_full, out_proxy, out_audio, limits) -> MediaMeta
  ```
- 認証：FastAPI の依存関数で `Authorization` を照合（`secrets.compare_digest`）。`/v1/health` は除外。
- SSE：`sse-starlette` の `EventSourceResponse`。接続時に snapshot、`Last-Event-ID` 以降を `events.jsonl` から再送、その後ライブ。
- 取り消し：サブプロセスは `start_new_session=True`（POSIX）/ `CREATE_NEW_PROCESS_GROUP`（Windows）で起動し、グループごと終了。

## タスク分解
1. FastAPI 骨格、設定（環境変数）、認証、エラー形式、`/health`・`/capabilities`。
2. domain（Job 状態機械、Upload）＋テスト。
3. `FsArtifactStore`、`FsJobRepository`、`events.jsonl` の EventBus ＋テスト。
4. アップロード API。
5. キューとワーカー（依存待ち、取り消し、再起動時の処理）＋テスト（`FakePipelineRunner`）。
6. SSE（snapshot、再開、heartbeat）。
7. 契約テスト（OpenAPI の照合）。
8. 配備スクリプトと `DEPLOY.md`、Windows / Linux で起動確認、トンネル越しの疎通（Mac から）。

## P1 / スコープ外
- Docker イメージ（P1）、Modal の Web エンドポイント化（P2）、複数 GPU（P2）、ユーザー管理・監査（スコープ外、[ADR-0010](../../adr/0010-out-of-scope-enterprise-and-later.md)）。
