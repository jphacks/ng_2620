// ルーティングとプロバイダ（F15）。
//   /                         ホーム
//   /project/:id/{import|edit|export}
//   /settings/:tab?           設定（接続 / アバターライブラリ / 一般）
//   /#/dev/editor?fixture=v0  レーン B のエディタ単独起動

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createHashRouter, Navigate, RouterProvider } from "react-router-dom";
import { useHotkeyDispatcher } from "@/presentation/shared/commands/useCommands";
import type { Services } from "@/presentation/shared/services";
import { ServicesProvider } from "@/presentation/shared/services";
import { useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { loadPrefs } from "@/presentation/shared/stores/prefsStore";
import { TooltipProvider } from "@/presentation/shared/ui";
import { AppFrame, HomeRoute } from "./AppFrame";
import { DevEditorRoute } from "./routes/EditRoute";
import { ProjectRoute } from "./routes/ProjectRoute";
import { SettingsRoute } from "./routes/SettingsRoute";

const router = createHashRouter(
  [
    {
      element: <AppFrame />,
      children: [
        { path: "/", element: <HomeRoute /> },
        { path: "/project/:projectId/:workspace", element: <ProjectRoute /> },
        { path: "/settings/:tab?", element: <SettingsRoute /> },
        { path: "*", element: <Navigate to="/" replace /> },
      ],
    },
    { path: "/dev/editor", element: <DevEditorRoute /> },
  ],
  {
    future: {
      v7_relativeSplatPath: true,
      v7_fetcherPersist: true,
      v7_normalizeFormMethod: true,
      v7_partialHydration: true,
      v7_skipActionErrorRevalidation: true,
    },
  },
);

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function Boot({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  useHotkeyDispatcher();
  useEffect(() => {
    void Promise.all([useConnectionStore.getState().init(), loadPrefs()])
      .catch((e) => console.error("[boot]", e))
      .finally(() => setReady(true));
  }, []);
  return ready ? <>{children}</> : null;
}

export function AppRoot({ services }: { services: Services }) {
  useEffect(() => {
    // 起動時の自己診断（F15）：WebGL2 と fetch のストリーム（SSE）
    void services
      .diagnose()
      .then((d) =>
        console.info("[self-check]", {
          runtime: services.env.isTauri ? "tauri" : "browser",
          backend: services.env.backendMode,
          ...d,
        }),
      );
  }, [services]);
  return (
    <ServicesProvider services={services}>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <Boot>
            <RouterProvider router={router} future={{ v7_startTransition: true }} />
          </Boot>
        </TooltipProvider>
      </QueryClientProvider>
    </ServicesProvider>
  );
}
