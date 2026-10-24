# GPU サーバーの使い方（アプリ側の手引き）

デスクトップアプリ（レーン A・B）から GPU サーバーを呼ぶ手順。型と全エンドポイントは [`openapi.yaml`](openapi.yaml)、
意味の正本は [designdocs/architecture/contracts.md](../../designdocs/architecture/contracts.md)。

## 0. 全体の流れ

```
アプリ                                   GPU サーバー（services/gpu_server）            pipeline/
 ├ POST /v1/projects                    プロジェクト作成
 ├ POST /v1/uploads → PUT chunks →      動画を受け取り、ffprobe で検証
 │  POST .../complete                   → mediaId と MediaMeta（プロキシ寸法など）
 ├ POST /v1/projects/{id}/jobs analyze  キューに入る ─────────────▶ ingest(ffmpeg) → offline_app_lite.py
 │  GET  /v1/jobs/{id}/events (SSE) ◀── 段階ごとに artifact_ready                 → run_occlusion_stage.py
 │     meta / proxy_frames（ingest）、persons / person_thumbs / track_masks（track）、
 │     mesh_preview（mesh）、units / units_index（occlusion）
 ├ POST /v1/avatars（VRM）               VRM を登録 → avatarId
 ├ POST .../jobs render                 analyze の完了を待って開始 ───▶ avatar_app.py（--export_layers）
 │     layers / layers_index / preview_video
 │  （ここからアプリ内で WebGL 合成・手修正。サーバー往復なし）
 ├ POST .../jobs export（overrides 付き）render の完了を待って開始 ──▶ avatar_app.py（--hq --overrides）→ ffmpeg
 │     export_video（元の解像度・H.264・音声つき）
 └ GET /v1/jobs/{id}/artifacts/export_video  完成 MP4 をダウンロード
```

`render` と `export` は依存ジョブ（`analyzeJobId` / `renderJobId`）が終わる前に投入してよい。サーバーが待つ。
人物の選択は `persons` が届いた時点（`track` の完了）でできる。`mesh` と `occlusion` はその間も裏で進む。

## 1. 接続と認証

| 項目 | 値 |
|---|---|
| ベース URL | 接続設定の URL。ローカル `http://127.0.0.1:8765`、デモ `https://<機名>.<tailnet>.ts.net`、モック `http://127.0.0.1:8766` |
| 認証 | 全リクエストに `Authorization: Bearer <token>`。`GET /v1/health` だけ不要。モックのトークンは `mock` |
| 疎通確認 | `GET /v1/health` → `{status:"ok", version, apiVersion:1}`、`GET /v1/capabilities` → GPU 名・VRAM・入力上限・キュー長 |

## 2. エラー

失敗した API は HTTP 4xx/5xx と `{"error": {"code", "message", "detail"?}}` を返す。`code` で分岐する（文言は `message` をそのまま出してよいが、
アプリ側の文言は designdocs/ux/interaction.md §5）。

| code | HTTP | いつ | アプリの対処 |
|---|---|---|---|
| `UNAUTHORIZED` | 401 | トークン違い | 接続設定を開く |
| `NOT_FOUND` | 404 | ID 不明（サーバーのデータが消えた等） | 再アップロード・再実行。アバターなら再登録 |
| `VALIDATION_FAILED` | 422 | パラメータ不正 | バグとして報告（`detail.errors`） |
| `MEDIA_TOO_LONG` / `MEDIA_TOO_LARGE` / `MEDIA_UNSUPPORTED` | 422 | 入力制約（30 秒・長辺 1920px・mp4/mov の H.264/HEVC） | `detail` の上限値を出してトリミング等を案内 |
| `UPLOAD_INCOMPLETE` / `CHECKSUM_MISMATCH` | 409 | 断片不足・破損 | `detail.missingChunks` を再送 / 最初から |
| `AVATAR_INVALID` | 422 | VRM として読めない・ボーン不足 | 別の VRM |
| `NOT_IMPLEMENTED` | 501 | 予約機能（ProRes 等の形式、`units:prompt`、`/me`） | 「今後対応」 |

ジョブの中で起きた失敗（`NO_PERSON_FOUND`、`GPU_OOM`、`PIPELINE_FAILED`、`INTERRUPTED`、`DEPENDENCY_NOT_READY`）は
HTTP では返らず、ジョブの `status:"failed"` と `error`、SSE の `job_failed` で届く。

## 3. アップロード（分割・再開可能）

```http
POST /v1/projects                      {"name": "カフェで弾き語り"}            → 201 {"projectId": "prj_…"}
POST /v1/uploads                       {"projectId", "kind": "source", "filename", "size", "sha256"}
                                                                                → 201 {"uploadId": "upl_…", "chunkSize": 8388608}
PUT  /v1/uploads/{uploadId}/chunks/{n} body = n 番目の chunkSize バイト（最後だけ短い）
                                       Content-Type: application/octet-stream  → {"receivedChunks": [0,1,…], "size"}
GET  /v1/uploads/{uploadId}            再開時：受信済みの断片                   → {"receivedChunks", "size"}
POST /v1/uploads/{uploadId}/complete   結合・sha256 照合・ffprobe 検証          → {"mediaId": "med_…", "meta": MediaMeta}
```

- `sha256` はファイル全体の 16 進 64 桁（アプリが事前に計算）。`complete` は冪等（2 回呼んでも同じ結果）。
- `meta`：`{durationSec, frameCount, fps, full:{width,height}, proxy:{width,height}, proxyScale, hasAudio, sourceCodec}`。
  回転は適用済み（縦動画は width < height）、`fps` は 30 以下の固定値、`full = proxy × proxyScale`（整数）。
  **プレビューのレイヤーはすべて `proxy` の寸法**、書き出しは `full` の寸法。

## 4. ジョブ

```http
POST /v1/projects/{projectId}/jobs   {"type": "analyze", "params": {"mediaId": "med_…", "maxTargets": 4}}
POST /v1/projects/{projectId}/jobs   {"type": "render",  "params": {"analyzeJobId": "job_…",
                                        "cast": [{"personId": 1, "action": "replace", "avatarId": "av_…"}],
                                        "finish": {"smooth": 0.05, "footLock": true, "spring": true}}}
POST /v1/projects/{projectId}/jobs   {"type": "export",  "params": {"renderJobId": "job_…",
                                        "overrides": [ …overrides.json の items… ],
                                        "settings": {"format": "mp4_h264", "crf": 18, "audio": true, "resolution": "full"}}}
→ 201 Job
GET  /v1/jobs/{jobId}                Job（SSE が使えないときのポーリング用）
GET  /v1/projects/{projectId}/jobs   Job[]
GET  /v1/projects/{projectId}        {projectId, name, createdAt, media[], jobs[]}（再接続時の復元）
GET  /v1/queue                       {items: [{jobId, projectId, type, status, position}]}（実行中は position -1）
POST /v1/jobs/{jobId}/cancel         取り消し（実行中ならパイプラインのプロセスを終了）
GET  /v1/jobs/{jobId}/logs?tail=200  ログ末尾（text/plain、「ログを見る」用）
```

`Job`：`{jobId, projectId, type, status, createdAt, startedAt?, finishedAt?, queuePosition?, dependsOn?, stages[], artifacts[], error?, params}`。
`stages[]` は `{name, status: pending|running|done|failed|skipped, progress: 0..1, etaSec?}`。

| type | stages（順） | 公開される成果物 |
|---|---|---|
| analyze | ingest → track → mesh → occlusion | meta, proxy_frames → persons, person_thumbs, track_masks → mesh_preview → units_index, units |
| render | removal → avatar → layers | layers_index, layers, preview_video, stats |
| export | composite → encode | export_video |

VRM：`POST /v1/avatars`（multipart の `file`）→ `{avatarId: "av_<sha256 先頭16桁>", meta}`。同じファイルは同じ ID。
`render` の前に `GET /v1/avatars/{avatarId}` が 404 なら登録し直す（サーバーのデータが消えた場合）。

## 5. 進捗（SSE）

```http
GET /v1/jobs/{jobId}/events
Authorization: Bearer <token>
Last-Event-ID: 42        ← 再接続時、最後に受け取った id
```

- **`EventSource` はヘッダーを付けられないので `fetch` のストリームで読む**（ADR-0012）。形式は標準の SSE（`id:` / `event:` / `data:` 行、空行で区切り）。
- 接続直後に `snapshot`（その時点の `Job`、`id` なし）。続いて保存済みのイベントのうち `Last-Event-ID` より後、その後はライブ。
- 15 秒ごとに `heartbeat`。`job_finished` / `job_failed` を送ったらサーバーが切る。
- 切れたら `Last-Event-ID` を付けて再接続（指数バックオフ、最大 30 秒）。それも駄目なら `GET /v1/jobs/{id}` のポーリング。

| event | data |
|---|---|
| `snapshot` | `Job` |
| `queued` | `{position}` |
| `stage_started` | `{stage}` |
| `progress` | `{stage, progress, etaSec?, message?}`（`message` 例 `"frame 120/300"`） |
| `artifact_ready` | `Artifact` = `{name, kind, stage, size, sha256, contentType, url}` |
| `stage_finished` | `{stage, durationSec}` |
| `job_finished` | `{status: "succeeded" \| "canceled"}` |
| `job_failed` | `{stage, error: {code, message}, logTail}` |

```ts
// 最小の読み方（TypeScript）
async function* sse(url: string, token: string, lastId?: string) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}`, ...(lastId ? { "Last-Event-ID": lastId } : {}) } });
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev: Record<string, string> = {};
      for (const line of block.split(/\r?\n/)) { const k = line.indexOf(":"); if (k > 0) ev[line.slice(0, k)] = line.slice(k + 1).trim(); }
      if (ev.event) yield { id: ev.id, event: ev.event, data: JSON.parse(ev.data ?? "{}") };
    }
  }
}
```

## 6. 成果物のダウンロード

```http
GET /v1/jobs/{jobId}/artifacts               Artifact[]
GET /v1/jobs/{jobId}/artifacts/{name}        本体。Range: bytes=a-b に対応（206）。動画のシークや再開に使える
```

`artifact_ready` の `url` をそのまま GET すればよい。`sha256` で検証してから `cache/<jobId>/` に置く（zip は展開）。

| name | 中身（contracts §4） |
|---|---|
| `meta` | `MediaMeta`（JSON） |
| `proxy_frames` | zip：`frames/00000.jpg …`（プロキシ、合成の「元フレーム」） |
| `persons` | JSON：`[{personId, firstFrame, lastFrame, visibleRatio, bestFrame, bbox:[x,y,w,h], thumb}]` |
| `person_thumbs` | zip：`person_thumbs/{personId}.jpg`（256px 正方） |
| `track_masks` | zip：`masks/00000.png`（8bit、値 = personId） |
| `mesh_preview` | MP4（H.264、プロキシ寸法） |
| `units_index` | JSON：`{units: [{unitId, frames:[first,last], label, tracks, areaPx, perPerson: {"<personId>": {auto, why, confidence, overlapFrames}}}]}` |
| `units` | zip：`units/00000.png`（**16bit**、値 = unitId、0 = なし） |
| `layers_index` | JSON：`{persons, order: {"<f>": [personId 奥→手前]}, avatarIds}` |
| `layers` | zip：`clean/00000.webp`、`avatar/{personId}/00000.webp`（RGBA ストレートアルファ）、`hide/{personId}/00000.png`（0/255） |
| `preview_video` | MP4：サーバーで合成したプレビュー（手修正なし、プロキシ寸法）。合成規則の一致確認用 |
| `export_video` | MP4：完成品（`full` 寸法、H.264 High、yuv420p、AAC、faststart） |
| `stats` | JSON：パイプラインの処理時間など（表示は任意） |

フレーム番号はすべて 0 始まり・5 桁ゼロ埋め。プレビューの合成規則は contracts §5。

## 7. 手修正（export の overrides）

`overrides.json` の `items` をそのまま `ExportParams.overrides` に入れる。サーバーが `units_index` の `tracks` に展開して
パイプラインの判定を上書きする（`front` = その区間はユニットがアバターを隠す、`behind` = 隠さない。同じフレームに複数あれば後のものが勝つ）。

```json
{"id": "ovr_01", "kind": "unit", "personId": 1, "unitId": 12, "range": [40, 95], "mode": "front", "createdAt": "…"}
```

`kind: "brush"`（P1）は受け付けるが、書き出しへの反映はまだ（無視される）。

## 8. 開発中はモックサーバー

```sh
cd services/gpu_server && uv venv .venv -p 3.11 && uv pip install -p .venv/bin/python -e '.[dev]'   # 初回
cd ../mock_server && ../gpu_server/.venv/bin/python -m mock_server        # http://127.0.0.1:8766  token: mock
```

同じ API・同じイベント・同じ名前と形式の成果物（中身は小さな仮のもの）を返す。`VVLOG_MOCK_SPEED`（倍速、既定 10）、
`VVLOG_MOCK_FAIL=track:NO_PERSON_FOUND`（失敗の再現）、`VVLOG_MOCK_DROP_SSE=5`（SSE 切断の再現）、
`VVLOG_MOCK_FIXTURE=fixtures/<name>`（実データの記録を再生）。詳しくは [services/mock_server/README.md](../../services/mock_server/README.md)。

通しの例は [services/gpu_server/scripts/smoke_e2e.py](../../services/gpu_server/scripts/smoke_e2e.py)（Python、モックにも実サーバーにも使える）。

## 9. curl での確認

```sh
T=mock; U=http://127.0.0.1:8766
curl -s $U/v1/health
P=$(curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"name":"t"}' $U/v1/projects | jq -r .projectId)
curl -s -H "Authorization: Bearer $T" $U/v1/projects/$P | jq
curl -N -H "Authorization: Bearer $T" $U/v1/jobs/<jobId>/events        # SSE をそのまま表示
```
