// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { WeightsPort } from "@/application/ports/weights";
import type { WeightsJob, WeightsStatus } from "@/domain/weights";
import { ja } from "@/presentation/shared/i18n/ja";
import type { Services } from "@/presentation/shared/services";
import { ServicesProvider } from "@/presentation/shared/services";
import { WeightsSection } from "./WeightsSection";

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(cleanup);

const T = ja.weights;
const file = {
  dest: "sam3/sam3.pt",
  gated: true,
  license: "SAM License",
  licenseUrl: "https://huggingface.co/facebook/sam3",
  bytes: 0,
};

function status(over: Partial<WeightsStatus> = {}): WeightsStatus {
  return {
    root: "C:\\Users\\a\\AppData\\Local\\vvlog\\weights",
    files: [{ ...file, state: "missing" }],
    allReady: false,
    freeBytes: 50 * 1024 ** 3,
    needsToken: true,
    job: { state: "idle" },
    ...over,
  };
}

function show(port: Partial<WeightsPort>) {
  const openUrl = vi.fn(async () => {});
  const services = { platform: { weights: port, shell: { openUrl } } } as unknown as Services;
  render(
    <ServicesProvider services={services}>
      <WeightsSection />
    </ServicesProvider>,
  );
  return { openUrl };
}

describe("WeightsSection", () => {
  it("フルモードでなければ（weights が無ければ）何も出さない", () => {
    const services = { platform: {} } as unknown as Services;
    const { container } = render(
      <ServicesProvider services={services}>
        <WeightsSection />
      </ServicesProvider>,
    );
    expect(container.textContent).toBe("");
  });

  it("未取得：許諾の案内とトークン入力（password）を出し、開始でトークンを渡して入力欄を空にする", async () => {
    const start = vi.fn(async (): Promise<WeightsJob> => ({ state: "running" }));
    show({
      status: async () => status(),
      start,
      watch: async () => null,
    });
    const input = (await screen.findByLabelText(T.tokenLabel)) as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(screen.getByText(/huggingface\.co\/facebook\/sam3/)).toBeTruthy();
    fireEvent.change(input, { target: { value: "hf_secret" } });
    fireEvent.click(screen.getByRole("button", { name: T.start }));
    await waitFor(() => expect(start).toHaveBeenCalledWith("hf_secret"));
    await waitFor(() => expect(input.value).toBe(""));
    expect(document.body.textContent).not.toContain("hf_secret");
  });

  it("HF_ACCESS_DENIED：案内を出し、許諾のページを開ける。途中のものは『続きから再開』", async () => {
    const { openUrl } = show({
      status: async () =>
        status({
          files: [{ ...file, state: "partial", bytes: 1024 }],
          job: {
            state: "failed",
            error: {
              code: "HF_ACCESS_DENIED",
              message: "許可されていません",
              detail: { url: "https://huggingface.co/facebook/sam3" },
            },
          },
        }),
    });
    expect(await screen.findByText(T.errors.HF_ACCESS_DENIED)).toBeTruthy();
    expect(screen.getByRole("button", { name: T.resume })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: T.openLicense }));
    expect(openUrl).toHaveBeenCalledWith("https://huggingface.co/facebook/sam3");
  });

  it("実行中：進捗を出し、開いた時点で実行中なら状態を流し読みする。中止できる", async () => {
    const cancel = vi.fn(async () => {});
    const running: WeightsJob = {
      state: "running",
      phase: "downloading",
      file: "sam3/sam3.pt",
      fileIndex: 0,
      fileCount: 3,
      bytesDone: 512 * 1024 ** 2,
      bytesTotal: 1024 ** 3,
    };
    const watch = vi.fn(async (on: (j: WeightsJob) => void) => {
      on(running);
      return running;
    });
    show({ status: async () => status({ job: running }), watch, cancel });
    expect(await screen.findByRole("progressbar")).toBeTruthy();
    expect(screen.getByText(/512 MB/)).toBeTruthy();
    expect(watch).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: T.cancel }));
    expect(cancel).toHaveBeenCalled();
  });

  it("取得済み：検証済みの表示だけ", async () => {
    show({
      status: async () => status({ allReady: true, files: [{ ...file, state: "ok", bytes: 1 }], needsToken: false }),
    });
    expect(await screen.findByText(T.ready)).toBeTruthy();
    expect(screen.queryByLabelText(T.tokenLabel)).toBeNull();
  });
});
