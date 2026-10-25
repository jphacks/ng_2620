# ADR-0014 サーバーは FastAPI＋GPU 1 枚につき 1 ワーカー。ステージはサブプロセスで実行する

- 状態：採択
- 日付：2026-10-24
- 補足：[ADR-0015](0015-web-first-windows-local-server.md) により、FastAPI＋サブプロセス実行は継続し、SPA 配信・ローカル FS API・media/hash API を加える。

## 文脈
- パイプラインは Python スクリプト群で、プロセスごとにモデルを読み込み、VRAM を数 GB 使う。`torch.compile` やグローバル状態も持つ。
- 既存の計測・検証は「スクリプトをプロセスとして起動」する形（`scripts/eval/e2e.py`）で行ってきた。
- GPU は 1 台に 1 枚（L4 / RTX 5060 Ti）。同時に 2 ジョブ走らせると VRAM が溢れ、時間も伸びる（2026-10 の計測で、並走させても合計時間はほぼ縮まなかった）。

## 決定
1. **FastAPI**（uvicorn）＋ pydantic。API 層は薄く、ユースケース（`application/`）を呼ぶだけ。
2. ジョブキューは**プロセス内の FIFO＋ GPU 1 枚につき 1 ワーカー**（asyncio タスク）。ジョブ状態は `VVLOG_DATA_ROOT/jobs/<jobId>/job.json` に保存し、サーバー再起動時に `running` を `failed（interrupted）` にして再投入を促す。
3. ステージは**パイプラインのスクリプトをサブプロセスで実行**する（`PipelineRunner` ポートの `SubprocessPipelineRunner`）。標準出力の `[STAGE]` 行・進捗行を解釈して進捗イベントに変換する。
   - 利点：既存スクリプトを大きく変えずに使える、クラッシュ（EGL の segfault など）がサーバーを巻き込まない、VRAM がプロセス終了で必ず解放される。
   - 欠点：ステージごとにモデル読み込み（数秒〜十数秒）がかかる。許容する（P2 で常駐ワーカー化）。
4. 成果物は `VVLOG_DATA_ROOT/projects/<projectId>/...` に置き、`ArtifactStore` ポート経由で読む（将来 S3 等に替えられる）。
5. 依存のピン：Colab（Python 3.13）で通った組み合わせ（`transformers~=4.57.3`、`diffusers==0.29.1`、`roma~=1.5.4`、`huggingface-hub<1.0`、`USE_TF=0 USE_JAX=0 USE_FLAX=0`）を `services/gpu_server/requirements.lock` に固定する。

## 却下した案
| 案 | 却下理由 |
|---|---|
| Celery / Redis | 1 GPU・1 ワーカーには過剰。配備物が増える |
| モデル常駐の 1 プロセス | 速いが、既存スクリプトの大改修とメモリ管理が要る。2 日では危険 |
| Modal のみ | コールドスタートとベンダー固定。将来のアダプタとして残す |
