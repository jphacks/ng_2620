// フルモード（ローカルアプリサーバーが画面を配信）：ドロップ取り込み → サーバーでの probe → 解析 → 人物の一覧（#39）

import { expect, test } from "@playwright/test";
import { existsSync, readdirSync } from "node:fs";
import { dropFile, ja, openFirstRecent, SAMPLE_AVATAR } from "./helpers";
import { RUN } from "./global-setup";
import { PORTS } from "./ports";

const BASE = `http://127.0.0.1:${PORTS.local}/`;

test.describe.configure({ mode: "serial" });

test("起動するとフルモードで、接続『この PC』につながっている（制限モードのバナーは出ない）", async ({ page }) => {
  await page.goto(BASE);
  await expect(page.getByText(ja.home.dropTitle)).toBeVisible();
  await expect(page.getByText(ja.connection.local.profileName).first()).toBeVisible();
  await expect(page.getByText(ja.restricted.title)).toHaveCount(0);
  // 接続できていれば『未接続』は出ない（接続のたびに health を確かめている）
  await expect(page.getByText(ja.home.notConnected)).toHaveCount(0);
});

test("動画をドロップ → サーバーへ取り込み → ffprobe の情報が出て、解析まで通る", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(BASE);
  await expect(page.getByText(ja.home.dropTitle)).toBeVisible();

  await dropFile(page, "clip.mp4", { type: "video/mp4" });
  // サーバーの ffprobe の結果（<video> では出ない『音声あり』『30fps』が出る）
  await expect(page.getByText("2.0 秒 / 360x640")).toBeVisible();
  await expect(page.getByText("30fps / 音声あり")).toBeVisible();

  await page.getByRole("button", { name: ja.importStep.start }).click();
  await expect(page.getByRole("button", { name: /人物を選ぶ/ })).toBeEnabled({ timeout: 60_000 });
  await page.getByRole("button", { name: /人物を選ぶ/ }).click();
  await expect(page.getByText("人物 1")).toBeVisible();
  await expect(page.getByText("人物 2")).toBeVisible();
  await expect(page.getByText(ja.status.saved)).toBeVisible(); // 解析の結果がプロジェクトに保存されてから次へ（自動保存は 1 秒後）
  expect(errors).toEqual([]);
});

test("アバター（VRM）をドロップして取り込める", async ({ page }) => {
  await page.goto(`${BASE}#/settings/avatars`);
  await dropFile(page, "avatar.vrm");
  await expect(page.getByText(SAMPLE_AVATAR).first()).toBeVisible();
  await expect(page.getByText("商用可")).toBeVisible();
});

test("キャスト：人物にアバターを割り当てて描画 → エディタが開く", async ({ page }) => {
  await openFirstRecent(page, BASE);
  // 開き直した直後は、ジョブの状態の復元の前後で『解析』か『キャスト』のどちらかの画面になる（どちらも正しい）。解析なら先へ進める
  const next = page.getByRole("button", { name: /人物を選ぶ/ });
  const checkbox = page.locator("button[role=checkbox], input[type=checkbox]").first();
  await expect(next.or(checkbox)).toBeVisible();
  if (await next.isVisible()) await next.click();
  await checkbox.click();
  await page.getByText(ja.avatar.choose).click();
  await page.getByText(SAMPLE_AVATAR).last().click();
  await page.getByRole("button", { name: ja.cast.render }).click();
  await expect(page).toHaveURL(/\/edit$/, { timeout: 60_000 });
  await expect(page.getByText(`人物 1 → ${SAMPLE_AVATAR}`)).toBeVisible();
});

test("書き出し：完了し、プロジェクトフォルダに MP4 ができる", async ({ page }) => {
  await openFirstRecent(page, BASE);
  await page.getByText(ja.workspace.export, { exact: true }).first().click();
  await page.getByRole("button", { name: ja.export.start }).click();
  await expect(page.getByText(ja.export.done).first()).toBeVisible({ timeout: 60_000 });
  const projects = `${RUN}/projects`;
  const dir = readdirSync(projects).find((n) => n.endsWith(".vvlog"))!;
  expect(existsSync(`${projects}/${dir}/project.json`)).toBe(true);
  expect(readdirSync(`${projects}/${dir}/exports`).some((f) => f.endsWith(".mp4"))).toBe(true);
});

test("ページを開き直しても、プロジェクトが残っていて続きから開ける", async ({ page }) => {
  await page.goto(BASE);
  await expect(page.locator("li button").first()).toBeVisible();
  await page.locator("li button").first().click();
  await expect(page).toHaveURL(/\/project\//);
  await expect(page.getByText(`人物 1 → ${SAMPLE_AVATAR}`).or(page.getByText(ja.export.done).first())).toBeVisible();
});

test("ショートカット：ブラウザの既定動作に負けずに働く（設定を開く・ジョブ一覧・保存）", async ({ page }) => {
  await openFirstRecent(page, BASE);
  await page.keyboard.press("Control+,");
  await expect(page).toHaveURL(/#\/settings/);
  await page.goto(`${BASE}#/`);
  // Ctrl+J はブラウザではダウンロード一覧、Ctrl+S はページの保存。どちらも画面のコマンドが先に取る
  await page.keyboard.press("Control+j");
  await expect(page.getByText(ja.jobsPanel.queue)).toBeVisible();
  const dialogs: string[] = [];
  page.on("dialog", (d) => dialogs.push(d.type()));
  await page.keyboard.press("Control+s");
  expect(dialogs).toEqual([]);
});
