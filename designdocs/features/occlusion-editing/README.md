# F10 occlusion-editing — 前後関係の手修正

| 項目 | 内容 |
|---|---|
| レーン | B（ユニットの生成と書き出しへの反映は C） |
| 優先度 | P0（ユニット単位）。ブラシ FR-19 と取りこぼし物体の追加 FR-20 は P1 |
| 関連 | FR-14, FR-15 / [ADR-0006](../../adr/0006-occlusion-overrides-model.md) / [contracts §4.1, §5, §6.2](../../architecture/contracts.md) / [ux/interaction §2](../../ux/interaction.md#2-前後関係の手修正f10の操作詳細) |
| 依存 | F09（ビューア・タイムライン・合成）、F04（`units`）、F01（`overrides.json` の保存）、F11（書き出しへの受け渡し） |

## ユーザーストーリー
- プロとして、アバターが机やギターの上に描かれてしまった箇所を、その物をクリックして「人物の前」にするだけで直したい。
- 逆に、背景の物のせいでアバターが欠けている箇所を「後ろ」にして直したい。
- 直した区間を調整したり、取り消したりしたい。

## 受け入れ条件
- [ ] ビューアでホバーしたユニットを薄くハイライト（遮蔽物レイヤー表示中、または `Alt` 押下中）。クリックで選択し、輪郭を表示。
- [ ] インスペクタ：ユニットの番号、出現区間、ターゲットごとの「自動判定（前 / 後ろ / 対象外）」と判定理由（`why` を平易な文に：D+ →「奥行きの差から手前と判定」、S+ →「形の重なりから手前と判定」、S- / D- →「後ろと判定」、Dinf →「遠くの背景と判定」）。
- [ ] 人物ごとに「自動 / 前 / 後ろ」を選べる（`F` / `B` / `A`）。既定の区間は `overlapFrames`（無ければ `frames`）。
- [ ] 変更はただちに合成に反映（0.5 秒以内）、`overrides.json` に自動保存。
- [ ] タイムラインの修正トラックに帯（ユニット番号と前 / 後ろ）。端のドラッグで区間変更、`I` / `O` で端を再生ヘッドに、`Delete` で削除。
- [ ] 元に戻す / やり直し（追加・変更・削除）。
- [ ] 同じ人物・ユニットで区間が重なる修正を追加したら、重なる部分は新しい方が勝つ（domain の区間演算で整理し、レコードを分割・統合する）。
- [ ] 書き出し（F11）に `overrides` が渡り、書き出した動画でも直っている（デモ素材で目視）。

## 設計
- domain/occlusion.ts：
  - `OccluderUnit { unitId, frames: [number, number], label, perPerson: Record<personId, { auto, why, confidence, overlapFrames }> }`
  - `OcclusionOverride { id, kind: "unit", personId, unitId, range: [number, number], mode: "front" | "behind" }`
  - `resolveModes(overrides, f): Map<(personId, unitId), mode>`（そのフレームで有効なモード。後勝ち）
  - `upsertOverride(list, o): list`（同じ人物・ユニットの重なりを整理）、`removeRange(list, personId, unitId, range)`
  - `explainWhy(why): string`
- application/usecases：`SelectUnitAt(f, x, y)`、`SetUnitMode(personId, unitId, mode, range?)`、`AdjustOverrideRange(id, range)`、`DeleteOverride(id)`、`Undo` / `Redo`（コマンドパターン：各ユースケースが `Command { do(), undo() }` を履歴に積む）。
- presentation/features/occlusion-edit：
  - インスペクタのセクション `UnitInspector`（F09 のインスペクタのスロットに登録）
  - タイムライントラック `OverrideTrack`・`UnitSpanTrack`（F09 の登録式トラック）
  - ビューアのホバー・選択（F09 の `viewer` にフック：`onHover(f, x, y)`、`onClick`）
  - `useOcclusionStore`：overrides、選択中ユニット、履歴
  - 合成への受け渡し：overrides が変わるたびに F09 の `compositor` の `overrideTable` を更新（`resolveModes` の結果）

## サーバー側（レーン C）
- `units` / `units.json`：[pipeline-integration §3.2](../../architecture/pipeline-integration.md#32-遮蔽物ユニット-adr-0006)
- 書き出しへの反映：[pipeline-integration §3.6](../../architecture/pipeline-integration.md#36-手修正の適用書き出し)

## タスク分解
1. domain/occlusion.ts ＋テスト（区間の重なり・分割・後勝ち、`resolveModes`）。— 依存なし
2. Undo/Redo の履歴（汎用 `CommandHistory`）＋テスト。
3. usecases 5 本＋テスト（`ProjectRepositoryPort.saveOverrides` はモック）。
4. ホバー・選択（座標→unitId、輪郭）。
5. `UnitInspector`、文言（`explainWhy`）。
6. `OverrideTrack` / `UnitSpanTrack`（ドラッグ、`I` / `O`、Delete）。
7. 合成への受け渡し（overrideTable 更新）と 0.5 秒の確認。
8. （P1）ブラシ：ビューア上で塗る（円ブラシ、サイズ `[` / `]`）、フレーム単位の `brush` レコード、合成の最後に適用。
9. （P1）取りこぼし物体の追加：`POST /v1/projects/{id}/units:prompt` を呼ぶ「新しい物体を指定」ツール（サーバー側は SAM3 の点プロンプト＋フロー持ち越し）。

## テスト
- フィクスチャ v0：四角ユニット #2 を「前」にすると、正解画像（v0 に同梱）と一致。
- 書き出し（実サーバー）で修正が反映されることを M2 で確認。

## P1 / スコープ外
- ブラシ（P1）、物体の追加（P1）、自信の低い判定の自動ハイライト（P1）、判定の一括変更（「この物体は常に前」をクリップ全体へ）は区間を全体にすれば同じなので専用 UI は作らない。
