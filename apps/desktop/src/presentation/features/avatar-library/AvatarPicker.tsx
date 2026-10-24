// アバターの選択（F06 のキャストの行で使う）。ライブラリから選ぶか、その場で .vrm を追加

import * as Popover from "@radix-ui/react-dialog";
import { ChevronDown, Plus } from "lucide-react";
import { useState } from "react";
import type { Avatar } from "@/domain/avatar";
import { ja } from "@/presentation/shared/i18n/ja";
import { useAvatars } from "@/presentation/shared/queries";
import { Button, cx } from "@/presentation/shared/ui";
import { AvatarThumb } from "./AvatarThumb";
import { LicenseBadge } from "./license";
import { useImportAvatars } from "./useImportAvatars";

export function AvatarPicker({
  value,
  onChange,
  disabled,
}: {
  value: string | null;
  onChange(avatarId: string | null): void;
  disabled?: boolean;
}) {
  const avatars = useAvatars();
  const { pickAndImport } = useImportAvatars();
  const [open, setOpen] = useState(false);
  const list = avatars.data ?? [];
  const selected = list.find((a) => a.avatarId === value) ?? null;

  const choose = (a: Avatar) => {
    onChange(a.avatarId);
    setOpen(false);
  };

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          disabled={disabled}
          className="flex h-9 min-w-[13rem] items-center gap-2 rounded border border-border bg-bg px-2 text-left hover:border-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          <AvatarThumb avatar={selected} size={24} />
          <span className={cx("min-w-0 flex-1 truncate", !selected && "text-faint")}>
            {selected ? selected.meta.name : ja.avatar.choose}
          </span>
          <ChevronDown size={14} className="text-muted" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Overlay className="fixed inset-0 z-40 bg-overlay" />
        <Popover.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[70vh] w-[520px] max-w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-panel p-4 shadow-2xl">
          <div className="flex items-center justify-between">
            <Popover.Title className="font-semibold">{ja.cast.library}</Popover.Title>
            <Button
              size="sm"
              icon={<Plus size={14} />}
              onClick={async () => {
                const added = await pickAndImport(false);
                if (added[0]) choose(added[0]);
              }}
            >
              {ja.cast.addVrm}
            </Button>
          </div>
          <Popover.Description className="sr-only">{ja.avatar.choose}</Popover.Description>
          <div className="mt-3 grid min-h-0 flex-1 grid-cols-3 gap-2 overflow-auto">
            {list.length === 0 && (
              <div className="col-span-3 p-6 text-center text-sm text-muted">{ja.avatar.empty}</div>
            )}
            {list.map((a) => (
              <button
                key={a.avatarId}
                type="button"
                onClick={() => choose(a)}
                className={cx(
                  "flex flex-col items-center gap-1.5 rounded border p-2 text-center hover:border-accent",
                  a.avatarId === value ? "border-accent bg-accent/10" : "border-border",
                )}
              >
                <AvatarThumb avatar={a} size={72} />
                <span className="w-full truncate text-sm">{a.meta.name}</span>
                <LicenseBadge avatar={a} />
              </button>
            ))}
          </div>
          {value && (
            <div className="mt-3 flex justify-end">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  onChange(null);
                  setOpen(false);
                }}
              >
                {ja.avatar.none}
              </Button>
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
