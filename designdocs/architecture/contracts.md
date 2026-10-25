# 契約（API・イベント・成果物・プロジェクト形式・合成規則）

**この文書が契約の正本**。`packages/contracts/openapi.yaml`・`project.schema.json`・`overrides.schema.json` はここから起こし、
差分が出たらこの文書を先に直す（[ADR-0003](../adr/0003-clean-architecture-and-contract-first.md)）。変更は `packages/contracts/CHANGELOG.md` に記録し、全レーンに告知する。

- 契約版：`apiVersion: 1`（パス `/v1`）。後方互換のない変更は `/v2`。
- 時刻は ISO 8601（UTC、`Z`）。ID は接頭辞付きの文字列（`prj_`、`job_`、`upl_`、`med_`）。アバターの ID は VRM ファイルの sha256 の先頭 16 桁（`av_<hex16>`）。
- フレーム番号 `f` は 0 始まり。ファイル名は 5 桁ゼロ埋め（`00042`）。
- 人物 ID `personId` はパイプラインの `pid`（1 始まりの整数）。

## 1. 共通

| 項目 | 内容 |
|---|---|
| ベース URL | 接続プロファイルの URL（例：`http://127.0.0.1:8765`、`https://gpu.example.ts.net`）＋ `/v1` |
| 認証 | `Authorization: Bearer <token>`。`GET /v1/health` だけ不要 |
| Content-Type | JSON は `application/json`。アップロードの断片は `application/octet-stream` |
| CORS | ブラウザから直接呼ぶ。リモートは `VVLOG_ALLOWED_ORIGINS`（画面を開くページのオリジン）と開発の `http://localhost:1420`。ローカルアプリは自分自身と `VVLOG_ALLOWED_ORIGINS` だけ（ADR-0015、DEPLOY.md §5） |

## 2. API

### 2.1 一覧

| メソッド・パス | 用途 | feature |
|---|---|---|
| `GET /v1/health` | 疎通確認（認証不要）→ `{status:"ok", version, apiVersion:1}` | F03 |
| `GET /v1/capabilities` | GPU 名・VRAM・入力上限・キュー長 | F03 |
| `POST /v1/projects` | サーバー側プロジェクトの作成 `{name}` → `{projectId}` | F01 / F02 |
| `GET /v1/projects/{projectId}` | メディア状態とジョブ一覧（再接続時の復元） | F01 |
| `DELETE /v1/projects/{projectId}` | サーバー側データの削除 | F01 |
| `POST /v1/uploads` | アップロード開始 `{projectId, kind:"source", filename, size, sha256}` → `{uploadId, chunkSize}` | F02 |
| `PUT /v1/uploads/{uploadId}/chunks/{index}` | 断片の送信（`chunkSize` バイト、最後だけ短い） | F02 |
| `GET /v1/uploads/{uploadId}` | 受信済みの断片 `{receivedChunks:[0,1,...], size}`（再開用） | F02 |
| `POST /v1/uploads/{uploadId}/complete` | 結合・sha256 検証・メディア検証 → `{mediaId, meta}` | F02 |
| `POST /v1/avatars` | VRM の登録（multipart `file`）→ `{avatarId, meta}`。同じ sha256 は登録済みを返す | F07 |
| `GET /v1/avatars/{avatarId}` | 登録確認（無ければ 404 → アプリが再送） | F07 |
| `POST /v1/projects/{projectId}/jobs` | ジョブ投入 `{type, params}` → `Job` | F04 / F08 / F11 |
| `GET /v1/jobs/{jobId}` | ジョブ状態（SSE のフォールバック） | F05 |
| `GET /v1/projects/{projectId}/jobs` | プロジェクトのジョブ一覧 | F05 |
| `GET /v1/queue` | サーバー全体のキュー `{items:[{jobId, projectId, type, status, position}]}` | F05 |
| `POST /v1/jobs/{jobId}/cancel` | 取り消し（実行中ならサブプロセスを終了） | F05 |
| `GET /v1/jobs/{jobId}/events` | SSE（§3.3） | F05 |
| `GET /v1/jobs/{jobId}/artifacts` | 成果物の一覧 `Artifact[]` | F05 |
| `GET /v1/jobs/{jobId}/artifacts/{name}` | 成果物の本体（`Range` 対応） | F05 / F09 |
| `GET /v1/jobs/{jobId}/logs?tail=200` | ログ末尾（`text/plain`） | F05 |
| `POST /v1/projects/{projectId}/units:prompt` | **P1** 取りこぼした遮蔽物をクリックで追加 `{frame, x, y, range}` → `{unitId}` | F10 |
| `GET /v1/me` | **予約**（エンタープライズ、501） | — |

### 2.2 型

```ts
type JobType = "analyze" | "render" | "export";
type JobStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";
type StageName = "ingest" | "track" | "mesh" | "occlusion"      // analyze
               | "removal" | "avatar" | "layers"                 // render
               | "composite" | "encode";                         // export

interface Job {
  jobId: string; projectId: string; type: JobType; status: JobStatus;
  createdAt: string; startedAt?: string; finishedAt?: string;
  queuePosition?: number;                 // queued のときだけ（0 = 次に実行）
  dependsOn?: string;                     // render → analyze の jobId、export → render の jobId
  stages: { name: StageName; status: "pending" | "running" | "done" | "failed" | "skipped";
            progress: number; /* 0..1 */ etaSec?: number; startedAt?: string; finishedAt?: string }[];
  artifacts: string[];                    // 公開済みの成果物名（§4）
  error?: ApiError["error"];
  params: AnalyzeParams | RenderParams | ExportParams;
}

interface AnalyzeParams { mediaId: string; maxTargets?: number /* 既定 4、上限 4 */ }

interface RenderParams {
  analyzeJobId: string;
  cast: { personId: number; action: "replace"; avatarId: string }[];   // action は将来 "erase" | "blur"（予約）
  finish?: { smooth?: number /* 既定 0.05 */; footLock?: boolean /* true */; spring?: boolean /* true */ };
}

interface ExportParams {
  renderJobId: string;
  overrides: OcclusionOverride[];        // §6.2
  settings: { format: "mp4_h264";        // 将来 "prores4444" | "png_seq" | "layers" | "vrma"（予約、501）
              crf?: number /* 18 */; audio?: boolean /* true */; resolution?: "full" | "proxy" };
}

interface Artifact { name: string; kind: ArtifactKind; stage: StageName; size: number; sha256: string;
                     contentType: string; url: string /* /v1/jobs/{jobId}/artifacts/{name} */ }

interface MediaMeta {                    // ingest が決める。後段はこれだけを見る（ADR-0011）
  durationSec: number; frameCount: number; fps: number;           // CFR 化後
  full: { width: number; height: number };                        // 回転適用後
  proxy: { width: number; height: number }; proxyScale: number;  // full = proxy * proxyScale（整数）
  hasAudio: boolean; sourceCodec: string;
}
```

### 2.3 エラー

```json
{ "error": { "code": "MEDIA_TOO_LONG", "message": "動画が 30 秒を超えています", "detail": { "durationSec": 42.1, "limitSec": 30 } } }
```

| code | HTTP | 発生 | アプリの対処 |
|---|---|---|---|
| `UNAUTHORIZED` | 401 | トークン違い | 接続設定を開く |
| `NOT_FOUND` | 404 | ID 不明（サーバーのデータが消えた等） | 再アップロード・再実行を案内 |
| `VALIDATION_FAILED` | 422 | パラメータ不正 | バグとして報告（ログ付き） |
| `MEDIA_TOO_LONG` / `MEDIA_TOO_LARGE` / `MEDIA_UNSUPPORTED` | 422 | 入力制約違反 | トリミング・縮小・変換を案内 |
| `UPLOAD_INCOMPLETE` / `CHECKSUM_MISMATCH` | 409 | 断片不足・破損 | 欠けた断片だけ再送 |
| `AVATAR_INVALID` | 422 | VRM として読めない・ボーン不足 | 別の VRM を選ぶ |
| `DEPENDENCY_NOT_READY` | 409 | 依存ジョブが未完了・失敗 | 依存ジョブの状態を表示 |
| `NO_PERSON_FOUND` | 200（ジョブ失敗） | 人物が検出されない | 人物が大きく映る動画を案内 |
| `GPU_OOM` | 200（ジョブ失敗） | VRAM 不足 | 再試行（他ジョブの終了後）、短い動画 |
| `PIPELINE_FAILED` | 200（ジョブ失敗） | その他の推論エラー | 再試行・ログ表示 |
| `INTERRUPTED` | 200（ジョブ失敗） | サーバー再起動で中断 | 再試行 |
| `CANCELED` | — | 取り消し | — |
| `NOT_IMPLEMENTED` | 501 | 予約機能 | 「今後対応」と表示 |

## 3. ジョブとイベント

### 3.1 ジョブの種類とステージ（[ADR-0004](../adr/0004-job-stages-and-progressive-results.md)）

| type | ステージ（順） | 依存 | 公開する成果物 |
|---|---|---|---|
| `analyze` | `ingest` → `track` → `mesh` → `occlusion` | アップロード完了 | `meta`, `proxy_frames`（ingest）/ `persons`, `person_thumbs`, `track_masks`（track）/ `mesh_preview`（mesh）/ `units`, `units_index`（occlusion） |
| `render` | `removal` → `avatar` → `layers` | analyze 成功 | `layers`, `layers_index`, `preview_video` |
| `export` | `composite` → `encode` | render 成功 | `export_video` |

### 3.2 状態遷移

```
queued ──(ワーカーが取得)──▶ running ──▶ succeeded
   │                           ├──▶ failed（error.code）
   └──(cancel)──▶ canceled ◀───┘（cancel）
サーバー再起動時: running → failed(INTERRUPTED)
```

### 3.3 SSE（`GET /v1/jobs/{jobId}/events`）

- 形式：`id: <連番>` / `event: <type>` / `data: <JSON>`。接続直後に**現在の状態のスナップショット**（`snapshot`）を 1 件送る。
- `Last-Event-ID` ヘッダーで再開できる（サーバーはジョブごとに全イベントを保持）。15 秒ごとに `heartbeat`。

| event | data |
|---|---|
| `snapshot` | `Job` |
| `queued` | `{position}` |
| `stage_started` | `{stage}` |
| `progress` | `{stage, progress /*0..1*/, etaSec?, message? /* "frame 120/300" */}` |
| `artifact_ready` | `Artifact` |
| `stage_finished` | `{stage, durationSec}` |
| `job_finished` | `{status: "succeeded" | "canceled"}` |
| `job_failed` | `{stage, error: {code, message}, logTail: string}` |
| `heartbeat` | `{}` |

## 4. 成果物（レイヤー）形式

全ファイルはプロキシ解像度（`meta.proxy`）。ただし `export_video` はフル解像度。zip は無圧縮（store）で、アプリは展開して `cache/<jobId>/` に置く。

| name | kind | stage | 形式 | 内容 |
|---|---|---|---|---|
| `meta` | `json` | ingest | `meta.json` | `MediaMeta` |
| `proxy_frames` | `zip` | ingest | `frames/{f}.jpg`（品質 90） | プロキシフレーム（＝合成の「元フレーム」） |
| `persons` | `json` | track | `persons.json` | `[{personId, firstFrame, lastFrame, visibleRatio, bestFrame, bbox:[x,y,w,h] /*bestFrame, proxy 座標*/, thumb:"person_thumbs/{personId}.jpg"}]` |
| `person_thumbs` | `zip` | track | `person_thumbs/{personId}.jpg`（256px 正方、bestFrame から切り出し） | 人物サムネイル |
| `track_masks` | `zip` | track | `masks/{f}.png`（8bit、0 = 背景、値 = personId） | 追跡マスク（段階的プレビュー用） |
| `mesh_preview` | `video` | mesh | `mesh_preview.mp4`（H.264、プロキシ解像度） | メッシュのオーバーレイ（パイプラインの `4d_*.mp4`） |
| `units` | `zip` | occlusion | `units/{f}.png`（**16bit** グレースケール、0 = なし、値 = unitId） | 遮蔽物ユニットのラベル画像。重なりは面積の小さいユニットを優先 |
| `units_index` | `json` | occlusion | `units.json` | §4.1 |
| `layers` | `zip` | layers | §4.2 | プレビュー合成の入力 |
| `layers_index` | `json` | layers | `layers.json` | `{persons:[personId...], order: {"<f>": [personId, ...] /* 奥→手前 */}, avatarIds: {"<personId>": avatarId}}` |
| `preview_video` | `video` | layers | `preview.mp4` | サーバー側で合成したプロキシ解像度の参照動画（手修正なし）。合成規則の一致確認とデモの保険 |
| `export_video` | `video` | encode | `export.mp4`（H.264 yuv420p、CRF 18、AAC 音声） | 完成動画 |

### 4.1 `units.json`

```json
{
  "units": [
    { "unitId": 12, "frames": [40, 133], "label": "object",            // label は "object" | "person"（ターゲット以外の人物）
      "tracks": [3, 17, 25],                                           // 連結した fusion v2 のトラック ID（サーバー内部用）
      "areaPx": 5230,                                                  // 出現フレームでの平均面積（プロキシ）
      "perPerson": {
        "1": { "auto": "front", "why": "D+", "confidence": 0.82, "overlapFrames": [40, 120] },
        "2": { "auto": "behind", "why": "S-", "confidence": 0.40, "overlapFrames": [] }
      } }
  ]
}
```

- `auto`：fusion v2 の判定（`front` = 人物の手前＝アバターを隠す / `behind` / `none` = 判定対象外（重ならない・条件外））。
- `overlapFrames`：その人物のメッシュと重なるフレーム範囲（UI で「このユニットが効く区間」を示す）。
- `confidence`：0..1。`why` と深度差 z から算出（UI で「自信の低い判定」を強調表示する、P1）。

### 4.2 `layers` zip の中身

```
clean/{f}.webp              背景復元（クリーンプレート）RGB、品質 90
avatar/{personId}/{f}.webp  アバター RGBA（ストレートアルファ）、品質 90（アルファはロスレス）
hide/{personId}/{f}.png     自動の隠れマスク 8bit（0 / 255）。アバター領域上で時間平滑化済み（avatar_app の AvatarHider と同じ）
```

## 5. 合成規則（composite rule）

プレビュー（アプリ WebGL）と書き出し（サーバー）で共通。フレーム `f`・画素 `x` について：

```
out ← clean[f](x)
for p in order[f]（奥 → 手前）:
    a ← alpha_p[f](x)                         // アバターのアルファ 0..1
    h ← effectiveHide(p, f, x)               // 0 or 1
    c ← (h == 1) ? frame[f](x) : avatarRGB_p[f](x)    // 隠す画素は元フレーム（＝手前の実物）を出す
    out ← out·(1 − a) + c·a

effectiveHide(p, f, x):
    h ← hide_p[f](x) ≥ 128
    u ← units[f](x)
    if u ≠ 0:
        m ← overrideMode(p, u, f)            // 該当する手修正のうち最後に追加したもの。無ければ null
        if m == "front":  h ← (alpha_p[f](x) > 0)
        if m == "behind": h ← 0
    for b in brushOverrides(p, f)（追加順、P1）:
        if b.mask(x): h ← (b.mode == "front")
    return h
```

- `overrideMode(p, u, f)`：`overrides` のうち `personId == p && unitId == u && range[0] ≤ f ≤ range[1]` を満たす最後のもの。
- **書き出し（サーバー）との差**：サーバーは手修正を fusion v2 の判定（トラック×人物の前/後ろ）に反映してから、`AvatarHider` で隠れマスクを作り直す（体の部位・時間平滑化を含む）。プレビューはユニットのマスクをそのまま使う近似。アバター同士の前後もプレビューは `order[f]`、書き出しは画素単位の z バッファ。
- **一致テスト**：手修正なしのとき、アプリの合成（CPU 参照実装 `domain/composite.ts`）とサーバーの `preview_video` の各フレームの差（|差| > 24/255 の画素）が 0.5% 未満であること（`packages/contracts/tests/composite/`）。

## 6. プロジェクトファイル形式（[ADR-0007](../adr/0007-project-file-format.md)）

### 6.1 `project.json`

```json
{
  "schemaVersion": 1,
  "id": "loc_6f1c…", "name": "カフェで弾き語り", "createdAt": "…", "updatedAt": "…",
  "source": { "path": "/Users/me/Movies/IMG_0001.MOV", "copied": false, "sha256": "…",
              "durationSec": 9.8, "width": 1080, "height": 1920, "fps": 29.97, "hasAudio": true },
  "compute": { "profileId": "remote-win", "serverProjectId": "prj_…", "mediaId": "med_…" },
  "jobs": {
    "analyze": { "jobId": "job_…", "status": "succeeded", "finishedAt": "…" },
    "render":  { "jobId": "job_…", "status": "succeeded", "finishedAt": "…", "castHash": "…" },
    "exports": [ { "jobId": "job_…", "status": "succeeded", "file": "exports/カフェで弾き語り_001.mp4", "overridesHash": "…" } ]
  },
  "meta": { "…": "MediaMeta（analyze の meta をコピー）" },
  "persons": [ { "personId": 1, "label": "人物 1", "firstFrame": 0, "lastFrame": 293, "visibleRatio": 0.97, "thumb": "cache/job_…/person_thumbs/1.jpg" } ],
  "cast": [ { "personId": 1, "action": "replace", "avatarId": "av_9a0c…" } ],
  "finish": { "smooth": 0.05, "footLock": true, "spring": true },
  "avatars": { "av_9a0c…": { "name": "Sample A", "file": "<ライブラリの絶対パス>", "sha256": "…" } },
  "ui": { "playhead": 120, "zoom": 1.0, "panelLayout": "default" },
  "sample": null,
  "owner": null, "createdBy": null
}
```

- `castHash` / `overridesHash`：現在のキャスト・手修正と、描画・書き出し時点のものが違えば「再描画が必要」「再書き出しが必要」をバッジ表示する。
- `persons[].label`：ユーザーが付けた人物名（既定「人物 n」、F06）。
- `sample`：同梱サンプルのとき `{ "id": "cafe", "license": "…", "exportedVideo": "exports/sample_fixed.mp4" }`、それ以外は `null`（F12）。
- `owner` / `createdBy`：エンタープライズ用の予約（今回は `null`）。

### 6.2 `overrides.json`

```json
{
  "schemaVersion": 1,
  "items": [
    { "id": "ovr_01", "kind": "unit", "personId": 1, "unitId": 12, "range": [40, 95], "mode": "front", "createdAt": "…" },
    { "id": "ovr_02", "kind": "brush", "personId": 1, "frame": 77, "mask": "brush/ovr_02.png", "mode": "behind", "createdAt": "…" }
  ]
}
```

- `kind: "brush"` は P1。マスクはプロキシ解像度の 8bit PNG（`brush/` 配下）。書き出し時は PNG を base64 で `ExportParams.overrides` に載せる。
- 元に戻す / やり直しはメモリ上の履歴（コマンドパターン）。保存されるのは現在の `items` だけ。

## 7. VRM のメタ情報（アプリで読む）

glTF の拡張 `VRM`（0.x：`meta.title`, `author`, `licenseName`, `allowedUserName`, `commercialUssageName`, `otherPermissionUrl`）または
`VRMC_vrm`（1.0：`meta.name`, `authors[]`, `licenseUrl`, `avatarPermission`, `commercialUsage`, `allowRedistribution`）から読み、`AvatarMeta` に正規化する：

```ts
interface AvatarMeta { name: string; authors: string[]; version: "0.x" | "1.0";
  license: { name?: string; url?: string; commercial?: string; avatarPermission?: string; redistribution?: boolean };
  thumbnail?: string /* glTF 内のサムネイル画像を PNG で取り出したもの */ }
```

## 8. 契約テスト

| テスト | 置き場所 | 内容 |
|---|---|---|
| OpenAPI と pydantic の一致 | `services/gpu_server/tests/test_contract.py` | FastAPI が出す OpenAPI と `packages/contracts/openapi.yaml` のスキーマ差分が無い |
| モックサーバーの準拠 | `services/mock_server/tests/` | すべてのエンドポイントが `openapi.yaml` の応答スキーマを満たす |
| 合成規則の一致 | `packages/contracts/tests/composite/` | 固定データ（`fixtures/`）で TS 参照実装・Python 実装・`preview_video` が一致 |
| プロジェクト形式 | `apps/desktop/src/domain/project.test.ts` | `project.schema.json` に合う／旧版からのマイグレーション |
