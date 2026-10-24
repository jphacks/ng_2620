# 用語集

ドキュメント・コード・UI 文言で用語を揃える。コード上の名前は右列を使う。

| 用語 | 意味 | コード上の名前 |
|---|---|---|
| プロジェクト | 動画 1 本と、その解析結果・割当・手修正・書き出し履歴のまとまり。ローカルではフォルダ `*.vvlog/` | `Project` |
| ソース動画 | ユーザーが取り込んだ元の動画ファイル | `SourceMedia` |
| プロキシ | 推論・プレビュー用に縮小したフレーム列（フル解像度の整数分の 1 で、短辺が 480px に最も近いもの。1080p → 540x960）。パイプラインの `images/` と同じ解像度 | `proxy` |
| フル解像度 | ソース動画の解像度。書き出しに使う（パイプラインの `--hq`） | `full` |
| 人物 | 解析で追跡された人。パイプラインの `pid`（1 始まりの整数） | `Person` / `personId` |
| ターゲット | 置換対象としてユーザーが選んだ人物 | `Target` |
| キャスト | ターゲットと VRM の割当の一覧 | `Cast` / `CastEntry` |
| アバター | VRM 0.x / 1.0 のモデル。アバターライブラリに取り込んで使う | `Avatar` / `avatarId` |
| ジョブ | GPU サーバーで実行する処理の単位。種別は `analyze` / `render` / `export`（[ADR-0004](adr/0004-job-stages-and-progressive-results.md)） | `Job` |
| ステージ | ジョブ内の段階（例：`track`、`mesh`、`occlusion`） | `Stage` |
| 成果物 | ジョブが作るファイル（人物一覧、マスク、レイヤー、書き出し動画など） | `Artifact` |
| レイヤー | プレビュー合成の入力。元フレーム・背景復元（クリーンプレート）・アバター RGBA・隠れマスク・遮蔽物ユニット | `Layer` |
| クリーンプレート | ターゲットの実体を消した背景フレーム（パイプラインの removal） | `clean` |
| 隠れマスク | アバターのうち、実物が手前にあるため隠す画素（fusion v2 の hide）。人物ごと・フレームごと | `hide` |
| 遮蔽物ユニット | 人物の手前・奥を判定する単位となる物体の領域。フレームをまたいで同じ ID を持つ | `OccluderUnit` / `unitId` |
| 自動判定 | fusion v2 がユニットごとに出した「前 / 後ろ」 | `autoDecision` |
| 手修正（オーバーライド） | ユーザーがユニット（P0）またはブラシ（P1）で上書きした前後関係 | `OcclusionOverride` |
| 有効な隠れマスク | 自動の隠れマスクに手修正を適用した結果。合成に使う（[contracts §合成規則](architecture/contracts.md#5-合成規則composite-rule)） | `effectiveHide()` |
| 接続プロファイル | GPU サーバーの接続先（ローカル / リモート、URL、トークン） | `ComputeProfile` |
| ComputeBackend | アプリから推論実行先を抽象化したポート（[ADR-0002](adr/0002-gpu-server-and-compute-backend-port.md)） | `ComputeBackendPort` |
| ワークスペース | 画面の大きな切り替え単位：取り込み / 編集 / 書き出し | `Workspace` |
| レーン | 並列開発の担当単位 A / B / C（[parallel-plan](architecture/parallel-plan.md)） | — |
| フィクスチャ | 実際の解析結果を固定データとして保存したもの。モックサーバーとテストで使う | `fixtures/` |
