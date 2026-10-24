# F06 cast-selection — 人物一覧から置換対象を選び、アバターを割り当てる

| 項目 | 内容 |
|---|---|
| レーン | A |
| 優先度 | P0 |
| 関連 | FR-09, FR-11 / [ux/screens S2-3](../../ux/screens.md#s2-3-キャスト) / [contracts §4 persons](../../architecture/contracts.md#4-成果物レイヤー形式) |
| 依存 | F05（`persons` 成果物の取得）、F07（アバターライブラリ）、F08（`render` の投入） |

## ユーザーストーリー
- 配信者として、動画に映る人の中から自分だけを選び、好きなアバターを割り当てたい。
- 複数人の動画では、人ごとに別のアバターを割り当てたい。選ばなかった人はそのまま残したい。

## 受け入れ条件
- [ ] `track` 完了で人物一覧（サムネイル、人物名「人物 n」、映っている区間、映っている割合）を表示。`mesh` / `occlusion` が実行中でも操作できる。
- [ ] 各人物にチェック（置換する）とアバターの選択。アバターはライブラリから選ぶか、行に `.vrm` をドロップ（F07 で取り込み）。
- [ ] 人物の行にカーソルを合わせると、プレビュー（追跡マスク）でその人物だけを強調する。
- [ ] 人物名は変更できる（`project.json` の `persons[].label`、既定「人物 n」）。
- [ ] 1 人以上選び、選んだ全員にアバターがあると「アバターを描画」が有効。押すと VRM をサーバーに登録（未登録のみ）→ `render` を投入（`dependsOn: analyze`）。
- [ ] 描画後にキャストを変えると、エディタ・書き出しに「再描画が必要」バッジ（`castHash` 比較）。[再描画] で `render` を再投入。
- [ ] 選んだ VRM のライセンス概要（作者・商用可否・改変可否）を行の下に表示。商用不可は黄色の注記。

## 設計
- domain/cast.ts：`Person { personId, label, firstFrame, lastFrame, visibleRatio, thumb }`、`CastEntry { personId, action: "replace", avatarId }`、`canRender(cast, persons): Result`、`castHash(cast, finish)`。
- usecases：`LoadPersons`（`persons.json` → domain）、`UpdateCast`、`RenamePerson`、`StartRender`（未登録 VRM の `registerAvatar` → `submitJob("render", {analyzeJobId, cast, finish})` → `project.jobs.render` 記録）。
- presentation/features/cast：`CastList`、`PersonRow`（サムネイル、チェック、アバター選択 `AvatarPicker`（F07 の部品））、マスクの強調プレビュー。エディタの左パネル「キャスト」でも同じ部品を使う（読み取り＋再描画ボタン）。

## タスク分解
1. domain：型、`canRender`、`castHash` ＋テスト。
2. usecases 4 本＋テスト。
3. `CastList` / `PersonRow` / 強調プレビュー（`track_masks` の該当 personId だけ着色）。
4. 「再描画が必要」バッジの判定と表示（エディタ・書き出しタブ）。

## P1 / スコープ外
- 人物ごとの仕上げパラメータ（P1）、消去・ぼかし（`action` の拡張、スコープ外）、途中から入る人物（P2）。
