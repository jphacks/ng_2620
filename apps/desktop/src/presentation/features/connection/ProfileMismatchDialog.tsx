// プロジェクトを開いたとき、解析した接続と既定の接続が違えば切り替えを提案する（F03）

import { ja } from "@/presentation/shared/i18n/ja";
import { useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, Dialog } from "@/presentation/shared/ui";

export function ProfileMismatchDialog() {
  const mismatch = useProjectStore((s) => s.profileMismatch);
  const dismiss = useProjectStore((s) => s.dismissMismatch);
  const profiles = useConnectionStore((s) => s.profiles);
  const select = useConnectionStore((s) => s.select);
  const target = profiles.find((p) => p.id === mismatch);
  return (
    <Dialog
      open={!!mismatch}
      onOpenChange={(o) => !o && dismiss()}
      title={ja.connection.switchTitle}
      description={target ? ja.connection.switchBody(target.name) : ja.connection.switchUnknown}
      footer={
        <>
          <Button onClick={dismiss}>{ja.connection.keep}</Button>
          {target && (
            <Button
              variant="primary"
              onClick={async () => {
                await select(target.id);
                dismiss();
              }}
            >
              {ja.connection.switch}
            </Button>
          )}
        </>
      }
    />
  );
}
