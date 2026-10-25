// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { BootstrapPort } from "@/application/ports/bootstrap";
import type { WeightsPort } from "@/application/ports/weights";
import type { BootstrapStatus } from "@/domain/bootstrap";
import type { WeightsStatus } from "@/domain/weights";
import { ja } from "@/presentation/shared/i18n/ja";
import type { Services } from "@/presentation/shared/services";
import { ServicesProvider } from "@/presentation/shared/services";
import { SetupPrompt } from "./SetupPrompt";

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
beforeEach(() => sessionStorage.clear());
afterEach(cleanup);

const T = ja.setup;

const bsStatus = (ready: boolean, verdict: "supported" | "unsupported" = "supported"): BootstrapStatus => ({
  ready,
  judgement: { verdict, reasons: verdict === "unsupported" ? ["NO_NVIDIA_DRIVER"] : [], warnings: [] },
  steps: [{ id: "venv", label: "Python 環境の作成", done: ready }],
  job: { state: "idle" },
});
const wsStatus = (allReady: boolean, needsToken = true): WeightsStatus => ({
  root: "C:\\w",
  files: [
    {
      dest: "sam3/sam3.pt",
      gated: true,
      license: null,
      licenseUrl: "https://huggingface.co/facebook/sam3",
      state: allReady ? "ok" : "missing",
      bytes: 0,
    },
  ],
  allReady,
  freeBytes: 50 * 1024 ** 3,
  needsToken: allReady ? false : needsToken,
  job: { state: "idle" },
});

function show(platform: { bootstrap?: Partial<BootstrapPort>; weights?: Partial<WeightsPort> }) {
  const services = { platform } as unknown as Services;
  return render(
    <ServicesProvider services={services}>
      <SetupPrompt />
    </ServicesProvider>,
  );
}

describe("SetupPrompt", () => {
  it("制限モード（ポートが無い）では何も出さない", () => {
    const { container } = show({});
    expect(container.textContent).toBe("");
  });

  it("すべて済んでいれば何も出さない", async () => {
    const bootstrap = { status: vi.fn(async () => bsStatus(true)) };
    const weights = { status: vi.fn(async () => wsStatus(true)) };
    const { container } = show({ bootstrap, weights });
    await waitFor(() => expect(weights.status).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("未完了なら起動時にダイアログを出し、あとで → バナーが残り、押すと再び開く", async () => {
    show({
      bootstrap: { status: vi.fn(async () => bsStatus(false)) },
      weights: { status: vi.fn(async () => wsStatus(false)) },
    });
    expect(await screen.findByText(T.title)).toBeTruthy();
    fireEvent.click(screen.getByText(T.later));
    await waitFor(() => expect(screen.queryByText(T.title)).toBeNull());
    expect(screen.getByRole("alert").textContent).toContain(T.banner);
    fireEvent.click(screen.getByText(T.open));
    expect(await screen.findByText(T.title)).toBeTruthy();
  });

  it("同じセッションで一度閉じていれば、ダイアログは出さずバナーだけ出す", async () => {
    sessionStorage.setItem("vvlog.setupPrompt.seen", "1");
    show({ weights: { status: vi.fn(async () => wsStatus(false)) } });
    expect((await screen.findByRole("alert")).textContent).toContain(T.banner);
    expect(screen.queryByText(T.title)).toBeNull();
  });

  it("トークンが要るときは、入れるまで開始できない。ボタン 1 つで環境 → 重みの順に進み、トークンは渡して消す", async () => {
    const order: string[] = [];
    let envReady = false;
    const bootstrap = {
      status: vi.fn(async () => bsStatus(envReady)),
      start: vi.fn(async () => {
        order.push("bootstrap");
        return { state: "running" as const };
      }),
      watch: vi.fn(async () => {
        envReady = true;
        return { state: "succeeded" as const };
      }),
    };
    const weights = {
      status: vi.fn(async () => wsStatus(false)),
      start: vi.fn(async (t?: string) => {
        order.push(`weights:${t}`);
        return { state: "running" as const };
      }),
      watch: vi.fn(async () => ({ state: "succeeded" as const })),
    };
    show({ bootstrap, weights });
    const btn = (await screen.findByText(T.start)) as HTMLButtonElement;
    expect(btn.closest("button")!.disabled).toBe(true);
    const input = screen.getByLabelText(T.tokenLabel) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "hf_abc" } });
    expect(btn.closest("button")!.disabled).toBe(false);
    fireEvent.click(btn);
    await waitFor(() => expect(weights.watch).toHaveBeenCalled());
    expect(order).toEqual(["bootstrap", "weights:hf_abc"]);
    await waitFor(() =>
      expect((screen.queryByLabelText(T.tokenLabel) as HTMLInputElement | null)?.value ?? "").toBe(""),
    );
  });

  it("GPU が非対応なら、開始ボタンは出さず、外部 GPU への案内を出す", async () => {
    show({
      bootstrap: { status: vi.fn(async () => bsStatus(false, "unsupported")) },
      weights: { status: vi.fn(async () => wsStatus(false)) },
    });
    expect(await screen.findByText(T.unsupportedTitle)).toBeTruthy();
    expect(screen.queryByText(T.start)).toBeNull();
  });
});
