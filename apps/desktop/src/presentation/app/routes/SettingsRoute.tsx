// S6 設定：接続 / アバターライブラリ / 一般

import { ArrowLeft } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { AvatarLibraryPanel } from "@/presentation/features/avatar-library";
import { ConnectionSettings } from "@/presentation/features/connection";
import { GeneralSettings } from "@/presentation/features/settings";
import { ja } from "@/presentation/shared/i18n/ja";
import { IconButton, TabPanel, Tabs } from "@/presentation/shared/ui";

type Tab = "connection" | "avatars" | "general";
const TABS: Tab[] = ["connection", "avatars", "general"];

export function SettingsRoute() {
  const navigate = useNavigate();
  const { tab } = useParams();
  const current: Tab = TABS.includes(tab as Tab) ? (tab as Tab) : "connection";
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 px-3 pt-3">
        <IconButton label={ja.common.back} icon={<ArrowLeft size={16} />} onClick={() => navigate(-1)} />
        <h2 className="text-lg font-semibold">{ja.settings.title}</h2>
      </div>
      <Tabs
        value={current}
        onChange={(t) => navigate(`/settings/${t}`, { replace: true })}
        tabs={TABS.map((t) => ({ value: t, label: ja.settings.tabs[t] }))}
        className="mt-2 flex-1"
      >
        <TabPanel value="connection">
          <ConnectionSettings />
        </TabPanel>
        <TabPanel value="avatars">
          <AvatarLibraryPanel />
        </TabPanel>
        <TabPanel value="general">
          <GeneralSettings />
        </TabPanel>
      </Tabs>
    </div>
  );
}
