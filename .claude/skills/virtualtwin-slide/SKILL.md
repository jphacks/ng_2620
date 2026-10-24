---
name: virtualtwin-slide
description: VirtualTwin のスライドを Marp で作る・直す・PDF にするときの手順。テーマ virtualtwin（docs/slide/themes/virtualtwin.css）の指定方法、使えるクラス（lead / invert / accent / cols など）、ロゴ・ページ番号・コピーライトの配置、書き出しコマンド、守ること（ロゴの扱い、スライドの置き場所）をまとめている。発表スライド、デモ資料、Marp の md を書く、スライドを PDF にする、スライドのデザインを変えると言われたときに使う。
---

# VirtualTwin のスライドの作り方

スライドは Marp（Markdown）で書く。置き場所は **`docs/slide/` 直下**。テーマは `docs/slide/themes/virtualtwin.css`。クラスの一覧と見本は `docs/slide/README.md` と `docs/slide/sample.md`。迷ったら先に見本を見る。

## 1. 書き出し方

先頭に次を書く。コピーライトは `footer` に入れる（最下部の中央に出る）。

```markdown
---
marp: true
theme: virtualtwin
paginate: true
footer: © 2026 VirtualTwin / ng_2620
---
```

PDF にする（リポジトリのルートで実行）。

```bash
npx @marp-team/marp-cli --theme-set docs/slide/themes/virtualtwin.css --allow-local-files --pdf docs/slide/<名前>.md -o docs/slide/<名前>.pdf
```

- `--allow-local-files` が無いと、ロゴ画像が PDF に入らない。
- 初回は `npx` の取得で時間がかかる。長いコマンドはバックグラウンドで走らせる。
- 書き出した PDF は、レビュー用なら置いてよいが、特に頼まれなければコミットしない。

## 2. 配置（テーマが決めている）

| 要素 | 場所 |
|---|---|
| 正方形マーク | 右下 |
| ページ番号 | 右上 |
| コピーライト（`footer`） | 最下部の中央 |

`lead` クラスのスライド（タイトル・章扉）には、マークもページ番号も出ない。タイトルスライドには `<!-- _paginate: false -->` も付ける。

## 3. クラス

スライドごとに `<!-- _class: lead -->` のように指定する。

| クラス | 用途 |
|---|---|
| `lead` | タイトル・章扉（緑のグラデーション、中央寄せ） |
| `invert` | 暗い緑の背景。暗い背景用のマークに切り替わる |
| `accent` | 黄色の背景（ひと息つくスライド） |
| `cols` | 2 カラム。直下に `<div>` を 2 つ置く |
| `small` | 本文を小さく |
| `nologo` | 右下のマークを出さない |

部品: `<div class="card">`（緑）、`<div class="card yellow">`（黄色）。`**太字**` は黄色い蛍光ペン、`*強調*` は黄色い文字になる。

## 4. 守ること

- **スライドは `docs/slide/` 直下に置く。** ロゴの `url()` は CSS ではなくスライドの場所から解決される。別の深さに置くなら CSS の `../../designdocs/...` を直す。
- **タイトルスライドのロゴは alt に `logo` を入れる**: `![w:480 logo](../../designdocs/brand/color/svg/virtualtwin-logo-horizontal.svg)`。中間の緑の上にカラー版を直置きしないよう、自動で白い板に載る。
- ロゴの色・縦横比・重なり方を変えない。ロゴの扱いは `virtualtwin-logo` skill と `designdocs/brand/README.md` に従う。
- 色は `themes/virtualtwin.css` の変数（`--vt-green-*` / `--vt-yellow-*`）を使い、直接 16 進数を書かない。足りない色は、ロゴの色（`designdocs/brand/README.md` §3）から選んでテーマに足す。
- フォントは Google Fonts の M PLUS Rounded 1c。オフラインでは Hiragino Maru Gothic ProN に落ちるので、字の幅が変わって改行位置がずれることがある。

## 5. 仕上げの確認

PDF（または `--images png`）に書き出して、次を目で見る。見ていないものは「見ていない」と言う。

- 右下のマークと本文が重なっていないか。
- 右上のページ番号と見出しの下線が重なっていないか。
- `invert` の表・コードの文字が読めるか。
- ロゴが出ているか（出ないときは、`designdocs/brand/` があるか、スライドの置き場所が合っているかを疑う）。
