---
name: virtualtwin-app-icon
description: VirtualTwin のアイコン（正方形マークの PNG・.ico・.icns）をアプリ（ブラウザ版の favicon・ランチャー・インストーラー）に差し込む、複数サイズの PNG を作り直す、別のサイズや形式のアイコンを書き出すときの手順。apps/desktop/assets/icons/ と apps/desktop/public/favicon.png の対応、既にあるサイズ違いの置き場所、icns/ico の作り方をまとめている。アプリのアイコンを差し替える、ファビコンや Dock のアイコンを作る、アイコンのサイズ違いが欲しいと言われたときに使う。
---

# VirtualTwin アプリアイコンの扱い

ロゴの使い方全般は `virtualtwin-logo` スキル。このスキルはアイコン（正方形マークのカラー版）の書き出しと、アプリへの差し込み。

## 1. 既にあるもの（`designdocs/brand/color/`）

| 種類 | 場所 |
|---|---|
| 元の SVG | `color/svg/virtualtwin-mark.svg` |
| サイズ違いの PNG（透過） | `color/png/mark-sizes/virtualtwin-mark-<16 / 32 / 48 / 64 / 128 / 256 / 512 / 1024>.png` |
| Windows 用 `.ico`（16 / 32 / 48 / 64 / 128 / 256px 入り） | `color/icon/virtualtwin.ico` |
| macOS 用 `.icns`（16〜1024px、`@2x` 込み 10 枚入り） | `color/icon/virtualtwin.icns` |

まず、これで足りるか確認する。足りなければ §3 で作り直す。

## 2. アプリに差し込む

Tauri は廃止した（ADR-0015）。アイコンは次の 2 か所にある。

| 場所 | 使われ方 |
|---|---|
| `apps/desktop/public/favicon.png` | ブラウザのタブのアイコン（`index.html`）。画面のビルドに入る |
| `apps/desktop/assets/icons/` | `icon.ico`（Windows のランチャー・インストーラー・ショートカット）、`icon.icns`（将来の macOS 向け）、各サイズの PNG。**ビルドには入らず**、ランチャー（#31）とインストーラー（#34）が参照する置き場 |

対応（コピー元 → コピー先）:

| コピー先 | コピー元 |
|---|---|
| `public/favicon.png` | `mark-sizes/virtualtwin-mark-32.png` |
| `assets/icons/32x32.png` | `mark-sizes/virtualtwin-mark-32.png` |
| `assets/icons/128x128.png` | `mark-sizes/virtualtwin-mark-128.png` |
| `assets/icons/128x128@2x.png` | `mark-sizes/virtualtwin-mark-256.png` |
| `assets/icons/icon.png` | `mark-sizes/virtualtwin-mark-512.png`（または 1024） |
| `assets/icons/icon.ico` | `color/icon/virtualtwin.ico` |
| `assets/icons/icon.icns` | `color/icon/virtualtwin.icns` |

- 差し替えたら、`pnpm --filter @vvlog/desktop build` が通り、ブラウザのタブのアイコンが変わったことを確かめる。ショートカット・インストーラーのアイコンは Windows の実機で見る（自動では確かめられない）。

## 3. サイズ違いや形式を作り直す

- **PNG**: SVG から直接書き出す（PNG を拡大縮小しない）。Chrome のヘッドレスが手早い。
  ```bash
  # 例: 256px。背景は透過。
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars \
    --default-background-color=00000000 --window-size=256,256 --force-device-scale-factor=1 \
    --screenshot=out-256.png "file:///path/to/page.html"
  ```
  `page.html` は、`<body style="margin:0;background:transparent"><img src="…/virtualtwin-mark.svg" width=256 height=256></body>` のような 1 枚のページ。
- **`.icns`**（macOS のみ）: `*.iconset` フォルダに `icon_16x16.png`, `icon_16x16@2x.png`（32px）, `icon_32x32.png`, `icon_32x32@2x.png`（64px）, `icon_128x128.png`, `icon_128x128@2x.png`（256px）, `icon_256x256.png`, `icon_256x256@2x.png`（512px）, `icon_512x512.png`, `icon_512x512@2x.png`（1024px）を置き、`iconutil -c icns <フォルダ> -o virtualtwin.icns`。
- **`.ico`**: 各サイズの PNG をそのまま格納する（Python の `struct` で、ヘッダー + ディレクトリ + PNG データを並べる）。256px は幅・高さを 0 と書く。

## 4. 注意

- マークは**角を丸めた状態で透過**。OS が自分で角を丸める用途（iOS、一部の Android）には、そのまま使えない。必要になったら、角を丸めない正方形版を作る（`designdocs/brand/README.md` の「まだないもの」）。
- 16px は細部が潰れて、白と黄色の丸が並んでいるのが分かる程度になる。ファビコンなどで見づらければ、簡略版を別に作る。
- 作り直したら `designdocs/brand/README.md` の「デスクトップアイコン用の複数サイズ」の項目も合わせて直す。
