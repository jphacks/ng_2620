# F01 project-management — プロジェクトの作成・保存・再開

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | A |
| 優先度 | P0 |
| 関連 | FR-01, FR-02 / [ADR-0007](../../adr/0007-project-file-format.md) / [contracts §6](../../architecture/contracts.md#6-プロジェクトファイル形式adr-0007) |
| 依存 | F15（枠）。サーバー側の復元は F03・F05 |

## ユーザーストーリー
- 配信者として、動画を入れたら自動でプロジェクトができ、閉じても続きから再開したい。
- プロとして、プロジェクトを好きな場所に保存し、別の日に開いて修正を続けたい。

## 受け入れ条件
- [ ] 新規：動画のドロップでプロジェクトが作られ、既定の保存先 `~/Movies/vvlog/<名前>.vvlog/` に `project.json` ができる。名前は動画ファイル名から（重複時は連番）。
- [ ] 保存：変更から 1 秒後に自動保存（デバウンス）、`⌘S` で即時。書き込みは一時ファイル → rename。ステータスバーに保存状態。
- [ ] 開く：ホームの最近のプロジェクト（最大 20 件、サムネイル・状態・更新日時）、`⌘O` でフォルダ選択。
- [ ] 再開：開いたときに `compute.serverProjectId` があれば `GET /v1/projects/{id}` で状態を照合し、実行中のジョブは SSE を再購読、完了済みで `cache/` に無い成果物は再取得。
- [ ] サーバー側に無い（404）場合は「サーバーのデータが見つかりません。解析をやり直しますか？」。
- [ ] `schemaVersion` が古いものはマイグレーションして開く。新しすぎるものは「新しい版のアプリで作られています」で開かない。
- [ ] 削除：ホームのコンテキストメニュー「ゴミ箱に移動」（ローカル）＋「サーバーのデータも削除」（`DELETE /v1/projects/{id}`、確認ダイアログ付き）。

## 設計
- domain/project.ts：`Project`、`SourceMedia`、`JobRef`、`CastEntry`、`migrate(json): Project`、`castHash()`、`overridesHash()`、`needsRender(project)`、`needsExport(project)`。
- application/ports：
  ```ts
  interface ProjectRepositoryPort {
    create(input: { name: string; dir?: string; source: SourceMedia }): Promise<Project>;
    open(path: string): Promise<Project>;
    save(project: Project): Promise<void>;              // 原子的
    saveOverrides(projectId: string, o: OverridesDoc): Promise<void>;
    loadOverrides(projectId: string): Promise<OverridesDoc>;
    listRecent(): Promise<RecentEntry[]>;
    remove(path: string): Promise<void>;                // ゴミ箱へ
    cacheDir(project: Project, jobId: string): string;
  }
  ```
- application/usecases：`CreateProject`、`OpenProject`（マイグレーション＋サーバー照合 `ReconcileWithServer`）、`SaveProject`（デバウンスは presentation 側）、`ListRecentProjects`、`DeleteProject`。
- infrastructure：`TauriProjectRepository`（Rust コマンド `write_atomic`、`read_text`、`move_to_trash`、最近の一覧はアプリデータ `recent.json`）、`InMemoryProjectRepository`（テスト・B の単独起動）。
- presentation/features/home：ドロップ領域（F02 と共有）、最近の一覧、サンプルを開く（F12）、接続表示。
- `useProjectStore`：現在のプロジェクト、dirty フラグ、自動保存。

## タスク分解
1. domain：型、`migrate`、ハッシュ関数、`needsRender/Export` ＋テスト。
2. JSON Schema（`packages/contracts/project.schema.json`）との照合テスト（ajv）。
3. Rust コマンド：`write_atomic(path, text)`、`read_text`、`move_to_trash`、`ensure_dir`。
4. `TauriProjectRepository` / `InMemoryProjectRepository`。
5. ユースケース 5 本＋テスト（ポートはモック）。
6. ホーム画面（最近の一覧、空状態）、自動保存フック、ステータスバー連携。
7. 再開時のサーバー照合（F05 の `SubscribeJob` を呼ぶ）。

## テスト
- 保存中に例外 → 元ファイルが壊れない（一時ファイルが残るだけ）。
- v0（仮の旧版）→ v1 のマイグレーション。

## P1 / スコープ外
- `.vvlog` を macOS のパッケージとして 1 ファイル表示（P2）、クラウド保存・共有（スコープ外）。
