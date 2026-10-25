import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./presentation/shared/index.css";
import { AppRoot } from "./presentation/app/AppRoot";
import { detectRuntime } from "./infrastructure/platform";
import { createServices } from "./presentation/app/container";

// 開発ビルドだけ：VITE_START_ROUTE=/dev/editor?fixture=v0 pnpm tauri dev で、その画面から始める
// （起動ごとに 1 回だけ。HMR の再読み込みでは戻さない）
if (import.meta.env.DEV && import.meta.env.VITE_START_ROUTE && !sessionStorage.getItem("vvlog.startRoute")) {
  sessionStorage.setItem("vvlog.startRoute", "1");
  window.location.hash = import.meta.env.VITE_START_ROUTE;
}

// どの動作モードか（ローカルサーバーが居るか）を先に確かめてから組み立てる
void detectRuntime().then((runtime) => {
  const services = createServices(runtime);
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <AppRoot services={services} />
    </StrictMode>,
  );
  void services.maintenance?.().catch((e) => console.warn("[maintenance]", e));
});
