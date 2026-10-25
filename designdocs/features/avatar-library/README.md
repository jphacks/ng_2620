# F07 avatar-library — VRM の取り込み・一覧・ライセンス表示

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | A |
| 優先度 | P0 |
| 関連 | FR-10 / [contracts §7](../../architecture/contracts.md#7-vrm-のメタ情報アプリで読む) / [ADR-0008](../../adr/0008-implementation-repo-and-model-placement.md)（VRM は同梱しない） |
| 依存 | F15。サーバーへの登録は F03 の `registerAvatar` |

## ユーザーストーリー
- 配信者として、自分の VRM を一度取り込めば、どのプロジェクトでも選べるようにしたい。
- プロとして、使う VRM の作者とライセンス（商用可否・改変可否）を確認してから使いたい。

## 受け入れ条件
- [ ] `.vrm`（VRM 0.x / 1.0）を取り込める（設定のライブラリ、キャストの行へのドロップ、ファイル選択）。
- [ ] 取り込み時に検証：glTF（GLB）として読める、`VRM` か `VRMC_vrm` 拡張がある、ヒューマノイドの必須ボーン（hips, spine, head, 四肢）がある。失敗は `AVATAR_INVALID` 相当の文言。
- [ ] ライブラリは アプリデータ（`~/Library/Application Support/<app>/avatars/`）に sha256 名でコピーし、`library.json` に名前・作者・ライセンス・サムネイル・取り込み日時。
- [ ] グリッド表示（サムネイル、名前、作者、ライセンスのバッジ：商用可 / 不可 / 不明）。詳細ダイアログでメタ情報の全項目と、利用条件の URL。
- [ ] 同じファイル（sha256 一致）の再取り込みは重複させない。
- [ ] 削除（使用中のプロジェクトがあれば警告。プロジェクトは avatarId と sha256 を持つので、再取り込みで復元できる）。

## 設計
- domain：`Avatar { avatarId /* av_<sha256 先頭16> */, sha256, file, meta: AvatarMeta, importedAt }`、`licenseBadge(meta): "commercial_ok" | "commercial_ng" | "unknown"`。
- ports：`AvatarLibraryPort { list(); import(path): Promise<Avatar>; remove(id); readBytes(id): Promise<Uint8Array> }`。
- infrastructure：
  - `TauriAvatarLibrary`（コピー・sha256・`library.json`）。
  - `vrmMeta.ts`：GLB の JSON チャンクを読み、`extensions.VRM.meta`（0.x）/ `extensions.VRMC_vrm.meta`（1.0）を `AvatarMeta` に正規化。サムネイルは 0.x の `meta.texture`、1.0 の `meta.thumbnailImage` が指す画像を取り出す。無ければ汎用アイコン。
  - （P1）3D の回転プレビュー：`@pixiv/three-vrm` で小さなキャンバスに表示。
- presentation/features/avatar-library：`AvatarGrid`、`AvatarDetailDialog`、`AvatarPicker`（F06 で使う）。

## タスク分解
1. `vrmMeta.ts`（GLB パーサ、0.x / 1.0 の正規化、必須ボーン検査）＋テスト（手元の VRM で。リポジトリにはコミットしない → テストは合成した最小 GLB）。
2. domain（ライセンスバッジ）＋テスト。
3. `TauriAvatarLibrary`（Rust：コピー、sha256 は F02 と共有）。
4. 画面：グリッド、詳細、ピッカー、ドロップ。

## P1 / スコープ外
- 3D プレビュー（P1）、VRoid Hub 連携（スコープ外）、VRM の表情・ポーズの調整（スコープ外）。
