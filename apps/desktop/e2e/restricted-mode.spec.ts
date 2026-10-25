// 制限モード（Pages 相当：ローカルサーバーなしの静的配信。/ng_2620/ の下）：案内・外部 GPU（BYO）の登録と診断・
// IndexedDB への保存・zip の読み込み／書き出し・動画の選び直し・解析（#39、#27、#44）

import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";
import { ASSETS } from "./global-setup";
import { ja } from "./helpers";
import { PORTS } from "./ports";

const PAGES = `http://127.0.0.1:${PORTS.staticPages}/ng_2620/`;
const GPU = `http://127.0.0.1:${PORTS.mockGpu}`;

test.describe.configure({ mode: "serial" });

// 制限モードの保存先はブラウザの IndexedDB。Playwright は既定でテストごとに新しい（空の）ブラウザコンテキストを作るので、
// 『保存して、あとで開く』を確かめるこのファイルでは、全テストで同じページ（＝同じ IndexedDB）を使う
let page: Page;
test.beforeAll(async ({ browser }) => {
  page = await (await browser.newContext()).newPage();
});
test.afterAll(async () => {
  await page.context().close();
});

async function addProfile(name: string, url: string, token: string) {
  await page.goto(`${PAGES}#/settings/connection`);
  // 一覧が空のときは『プロファイルを追加』、あるときは一覧の上の『追加』
  await page
    .getByRole("button", { name: new RegExp(`^(${ja.common.add}|${ja.connection.add})$`) })
    .first()
    .click();
  await page.locator("#pf-name").fill(name);
  await page.locator("#pf-url").fill(url);
  await page.locator("#pf-token").fill(token);
}

test("サブパスで配信された同じビルドが動き、制限モードの案内が出る（ローカルサーバーは無い）", async () => {
  const failed: string[] = [];
  page.on(
    "response",
    (r) => r.status() >= 400 && !r.url().includes("/v1/local/info") && failed.push(`${r.status()} ${r.url()}`),
  );
  await page.goto(PAGES);
  await expect(page.getByText(ja.home.dropTitle)).toBeVisible();
  await expect(page.getByText(ja.restricted.title)).toBeVisible();
  await expect(page.getByText(ja.restricted.find)).toHaveCount(0); // http のページでは『ローカルアプリを探す』は出ない
  expect(failed).toEqual([]);
});

test("外部 GPU を登録して接続テスト → 通る。再読み込みしても残る（IndexedDB）", async () => {
  await addProfile("自前のGPU", GPU, "mock");
  await page.getByRole("button", { name: ja.connection.test }).click();
  await expect(page.getByText(ja.connection.testOk)).toBeVisible();
  await page.getByRole("button", { name: ja.common.save }).click();
  await page.reload();
  await expect(page.getByText("自前のGPU").first()).toBeVisible();
  // 最初に登録した接続は既定になり、再読み込みしてもその接続につながる
  await expect(page.getByText(ja.home.notConnected)).toHaveCount(0);
});

test("接続テストの失敗理由：CORS で拒否／届かない、をブラウザで区別して案内する", async () => {
  // 起動しているが、この画面のオリジンを許していないサーバー（ローカルアプリ）→ 届いてはいるので CORS
  await addProfile("許可していない所", `http://127.0.0.1:${PORTS.local}`, "mock");
  await page.getByRole("button", { name: ja.connection.test }).click();
  await expect(page.getByText(ja.connection.testFailed.reach, { exact: true })).toBeVisible();
  await expect(page.getByText(/VVLOG_ALLOWED_ORIGINS/)).toBeVisible();
  await expect(page.getByText(`http://127.0.0.1:${PORTS.staticPages}`, { exact: false }).first()).toBeVisible();

  // 何も動いていない所 → 届かない
  await page.locator("#pf-url").fill("http://127.0.0.1:9");
  await page.getByRole("button", { name: ja.connection.test }).click();
  await expect(page.getByText(ja.connection.testFailed.reach, { exact: true })).toBeVisible();
  await expect(page.getByText(/VVLOG_ALLOWED_ORIGINS/)).toHaveCount(0);
  await expect(page.getByText(/サーバーに届きません。URL/)).toBeVisible();
});

test("別の PC で作られた zip を読み込む → 元の動画を選び直す → 外部 GPU へ送って解析まで通る", async () => {
  await page.goto(PAGES);
  await expect(page.getByText(ja.home.notConnected)).toHaveCount(0); // 既定の外部 GPU につながっている
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: ja.home.openZip }).click();
  await (await chooser).setFiles(`${ASSETS}/project.vvlog.zip`);

  // 元の動画が無い：選び直しが出て、解析は始められない
  await expect(page.getByText(ja.importStep.sourceMissingTitle)).toBeVisible();
  await expect(page.getByRole("button", { name: ja.importStep.start })).toBeDisabled();
  const chooser2 = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: ja.importStep.relink }).click();
  await (await chooser2).setFiles(`${ASSETS}/clip.mp4`);
  await expect(page.getByText(ja.importStep.sourceMissingTitle)).toHaveCount(0);

  // 外部サーバーへ初めて送るので、送信先の確認が出る
  await page.getByRole("button", { name: ja.importStep.start }).click();
  await expect(page.getByText(ja.connection.consent.title)).toBeVisible();
  await page.getByRole("button", { name: ja.connection.consent.ok }).click();
  await expect(page.getByRole("button", { name: /人物を選ぶ/ })).toBeEnabled({ timeout: 60_000 });
});

test("プロジェクトは IndexedDB に残り、zip で書き出せる（再読み込みの後でも）", async () => {
  await page.goto(PAGES);
  await page.reload();
  const row = page.locator("li button").first();
  await expect(row).toBeVisible();
  await row.click({ button: "right" });
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: ja.home.saveZip }).click();
  const d = await download;
  expect(d.suggestedFilename()).toMatch(/\.vvlog\.zip$/);
  const bytes = readFileSync(await d.path());
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
  expect(bytes.includes(Buffer.from("project.json"))).toBe(true);
  expect(bytes.includes(Buffer.from("cache/"))).toBe(false); // cache/ は入れない
});
