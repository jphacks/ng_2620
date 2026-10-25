// @vitest-environment jsdom
// 外部サーバーへ初めて動画を送るときは確認を挟み、確認した後は挟まない（#44、#35 BYO）

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ComputeProfile } from "@/domain/connection";
import { ja } from "@/presentation/shared/i18n/ja";
import type { Services } from "@/presentation/shared/services";
import { ServicesProvider } from "@/presentation/shared/services";
import { useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { useImportStore } from "./importStore";
import { VideoStep } from "./VideoStep";

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const remote: ComputeProfile = {
  id: "gpu1",
  name: "自前 GPU",
  kind: "remote",
  baseUrl: "https://gpu.example",
  tokenRef: "gpu1",
};
const local: ComputeProfile = {
  id: "local-gpu",
  name: "この PC",
  kind: "local",
  baseUrl: "http://127.0.0.1:8765",
  tokenRef: "local-gpu",
};

function setup(profile: ComputeProfile, consents: Record<string, string>) {
  const start = vi.fn(async () => {});
  const onStarted = vi.fn();
  const consent = vi.fn(async (p: ComputeProfile) => {
    useConnectionStore.setState({ consents: { ...useConnectionStore.getState().consents, [p.id]: p.baseUrl } });
  });
  useImportStore.setState({ start, runs: {} } as never);
  useProjectStore.setState({
    current: {
      project: {
        id: "prj1",
        source: { path: "/mem/drops/a/clip.mp4", durationSec: 5, width: 360, height: 640, fps: 30, hasAudio: true },
        compute: {},
        meta: undefined,
      },
    },
  } as never);
  useConnectionStore.setState({
    profiles: [profile],
    activeProfileId: profile.id,
    state: { status: "connected", profileId: profile.id, failures: 0, everConnected: true },
    capabilities: null,
    consents,
    consent,
  } as never);
  const services = {
    platform: { files: { toUrl: () => "blob:x", exists: async () => true } },
    uc: {},
  } as unknown as Services;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ServicesProvider services={services}>
        <VideoStep onCancel={() => {}} onStarted={onStarted} onOpenConnection={() => {}} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { start, onStarted, consent };
}

const startButton = () => screen.getByRole("button", { name: ja.importStep.start });

describe("VideoStep の送信先の確認", () => {
  it("外部サーバーへ初めて送るとき：確認を出し、同意するまで始めない", async () => {
    const { start, onStarted, consent } = setup(remote, {});
    await act(async () => fireEvent.click(startButton()));
    expect(screen.getByText(ja.connection.consent.title)).toBeTruthy();
    expect(screen.getByText(ja.connection.consent.body("自前 GPU", "https://gpu.example"))).toBeTruthy();
    expect(screen.getByText(ja.connection.consent.note)).toBeTruthy();
    expect(start).not.toHaveBeenCalled();

    await act(async () => fireEvent.click(screen.getByRole("button", { name: ja.connection.consent.ok })));
    expect(consent).toHaveBeenCalledWith(remote);
    await vi.waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    expect(onStarted).toHaveBeenCalledTimes(1);
  });

  it("キャンセルすると、始めず、同意も記録しない", async () => {
    const { start, consent } = setup(remote, {});
    await act(async () => fireEvent.click(startButton()));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: ja.common.cancel, hidden: false })));
    expect(start).not.toHaveBeenCalled();
    expect(consent).not.toHaveBeenCalled();
  });

  it("同じ宛先で確認済みなら、確認なしでそのまま始める", async () => {
    const { start, onStarted } = setup(remote, { gpu1: "https://gpu.example" });
    await act(async () => fireEvent.click(startButton()));
    expect(screen.queryByText(ja.connection.consent.title)).toBeNull();
    expect(start).toHaveBeenCalledTimes(1);
    expect(onStarted).toHaveBeenCalledTimes(1);
  });

  it("確認したあとで URL が変わっていたら、確認し直す", async () => {
    const { start } = setup(remote, { gpu1: "https://old.example" });
    await act(async () => fireEvent.click(startButton()));
    expect(screen.getByText(ja.connection.consent.title)).toBeTruthy();
    expect(start).not.toHaveBeenCalled();
  });

  it("この PC（ローカルアプリ）には確認を出さない", async () => {
    const { start } = setup(local, {});
    await act(async () => fireEvent.click(startButton()));
    expect(screen.queryByText(ja.connection.consent.title)).toBeNull();
    expect(start).toHaveBeenCalledTimes(1);
  });
});
