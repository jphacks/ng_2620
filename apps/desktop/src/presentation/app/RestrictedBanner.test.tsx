// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { LocalAppPort, LocalAppProbe } from "@/application/ports/system";
import { ja } from "@/presentation/shared/i18n/ja";
import type { Services } from "@/presentation/shared/services";
import { ServicesProvider } from "@/presentation/shared/services";
import { RestrictedBanner } from "./RestrictedBanner";

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

const T = ja.restricted;

function show(opts: { mode?: "restricted" | "full"; localApp?: LocalAppPort }) {
  const services = {
    env: { mode: opts.mode ?? "restricted" },
    platform: { localApp: opts.localApp },
  } as unknown as Services;
  return render(
    <ServicesProvider services={services}>
      <RestrictedBanner />
    </ServicesProvider>,
  );
}

const app = (probe: () => Promise<LocalAppProbe>, canProbe = true): LocalAppPort => ({
  url: "http://127.0.0.1:8765/",
  canProbe,
  probe,
});

describe("RestrictedBanner", () => {
  it("制限モードでだけ出る。フルモードでは出ない", () => {
    show({ mode: "full" });
    expect(screen.queryByText(T.title)).toBeNull();
    cleanup();
    show({});
    expect(screen.getByText(T.title)).toBeTruthy();
  });

  it("閉じると、同じセッションの間は出ない", () => {
    const { unmount } = show({});
    fireEvent.click(screen.getByRole("button", { name: T.dismiss }));
    expect(screen.queryByText(T.title)).toBeNull();
    unmount();
    show({});
    expect(screen.queryByText(T.title)).toBeNull();
  });

  it("ローカルアプリの案内は、Pages（localApp あり）のときだけ", () => {
    show({});
    expect(screen.queryByText(T.find)).toBeNull();
    cleanup();
    show({ localApp: app(async () => ({ found: false })) });
    expect(screen.getByRole("button", { name: T.find })).toBeTruthy();
  });

  it("探すボタンは押したときだけ探し、見つかればローカル版を開くリンクが出る", async () => {
    const probe = vi.fn(async (): Promise<LocalAppProbe> => ({ found: true, url: "http://127.0.0.1:8765/" }));
    show({ localApp: app(probe) });
    expect(probe).not.toHaveBeenCalled(); // 表示しただけでは何も呼ばない（ブラウザの許可確認を勝手に出さない）
    await act(async () => fireEvent.click(screen.getByRole("button", { name: T.find })));
    expect(probe).toHaveBeenCalledTimes(1);
    expect(screen.getByText(T.foundBody)).toBeTruthy();
    const link = screen.getByRole("link", { name: T.open }) as HTMLAnchorElement;
    expect(link.href).toBe("http://127.0.0.1:8765/");
  });

  it("見つからなければ、その旨とアドレスの案内を出す", async () => {
    show({ localApp: app(async () => ({ found: false })) });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: T.find })));
    expect(screen.getByText(T.notFound)).toBeTruthy();
    expect(screen.getByText(T.openByAddress("http://127.0.0.1:8765/"))).toBeTruthy();
  });

  it("探せないブラウザ（Safari など）は探すボタンを出さず、アドレスの案内だけ", () => {
    const probe = vi.fn(async (): Promise<LocalAppProbe> => ({ found: false }));
    show({ localApp: app(probe, false) });
    expect(screen.queryByRole("button", { name: T.find })).toBeNull();
    expect(screen.getByText(T.openByAddress("http://127.0.0.1:8765/"))).toBeTruthy();
    expect(probe).not.toHaveBeenCalled();
  });
});
