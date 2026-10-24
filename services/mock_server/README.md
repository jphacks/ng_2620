# services/mock_server

GPU なしで本物と同じ API・同じイベントの流れ・同じ名前の成果物を返すモックサーバー（designdocs/features/mock-server-fixtures）。
中身は本サーバー（`services/gpu_server`）そのもので、ステージだけを再生用（`mock_server/replay.py`）に差し替えている。

```sh
cd services/gpu_server && uv venv .venv -p 3.11 && uv pip install -p .venv/bin/python -e '.[dev]'   # 初回だけ
cd ../mock_server && ../gpu_server/.venv/bin/python -m mock_server            # http://127.0.0.1:8766  token: mock
```

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `VVLOG_MOCK_SPEED` | `10` | 再生の倍速（合成の進捗も、記録したフィクスチャも） |
| `VVLOG_MOCK_FIXTURE` | なし | `fixtures/<name>`。無ければ合成の進捗と小さな仮の成果物（名前と形式は本物と同じ） |
| `VVLOG_MOCK_FAIL` | なし | `track:NO_PERSON_FOUND` のように、そのステージをそのエラーで失敗させる |
| `VVLOG_MOCK_DROP_SSE` | なし | SSE を N 秒で切る（`Last-Event-ID` での再接続の確認用） |
| `VVLOG_DATA_ROOT` | 一時ディレクトリ | サーバーのデータの置き場 |

アップロードは本物と同じく ffprobe で検証する（Mac なら `brew install ffmpeg`）。ジョブの結果は動画に関係なく同じ。
