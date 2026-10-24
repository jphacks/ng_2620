// 入力部品（Radix Primitives ＋ デザイントークン）

import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import * as SelectPrimitive from "@radix-ui/react-select";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { Check, ChevronDown } from "lucide-react";
import { forwardRef } from "react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
export { cx };

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:brightness-110 disabled:bg-raised disabled:text-faint",
  secondary: "bg-raised text-fg border border-border hover:bg-panel-2 disabled:text-faint",
  ghost: "text-fg hover:bg-raised disabled:text-faint",
  danger: "bg-danger text-white hover:brightness-110 disabled:bg-raised disabled:text-faint",
};
const SIZE: Record<Size, string> = { sm: "h-7 px-2.5 text-sm gap-1.5", md: "h-8 px-3.5 text-base gap-2" };

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, className, children, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx(
        "inline-flex shrink-0 items-center justify-center rounded font-medium transition-colors disabled:cursor-not-allowed",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...rest}
    >
      {icon}
      {children}
    </button>
  );
});

export const IconButton = forwardRef<HTMLButtonElement, ButtonProps & { label: string }>(function IconButton(
  { label, className, size = "sm", variant = "ghost", ...rest },
  ref,
) {
  return (
    <Button
      ref={ref}
      aria-label={label}
      title={label}
      variant={variant}
      size={size}
      className={cx("!px-1.5", className)}
      {...rest}
    />
  );
});

export function Label({ children, htmlFor, className }: { children: ReactNode; htmlFor?: string; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cx("text-sm text-muted", className)}>
      {children}
    </label>
  );
}

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function TextInput(
  { className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cx(
        "h-8 w-full rounded border border-border bg-bg px-2.5 text-base text-fg placeholder:text-faint focus:border-accent focus:outline-none",
        className,
      )}
      {...rest}
    />
  );
});

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
  id,
}: {
  checked: boolean;
  onChange(v: boolean): void;
  label?: ReactNode;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <label className={cx("inline-flex items-center gap-2", disabled ? "text-faint" : "cursor-pointer")}>
      <CheckboxPrimitive.Root
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(v) => onChange(v === true)}
        className="flex h-4 w-4 items-center justify-center rounded-sm border border-border bg-bg data-[state=checked]:border-accent data-[state=checked]:bg-accent"
      >
        <CheckboxPrimitive.Indicator>
          <Check size={12} strokeWidth={3} className="text-accent-fg" />
        </CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>
      {label}
    </label>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange(v: boolean): void;
  label?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={cx("inline-flex items-center gap-2", disabled ? "text-faint" : "cursor-pointer")}>
      <SwitchPrimitive.Root
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
        className="relative h-[18px] w-8 rounded-full bg-raised transition-colors data-[state=checked]:bg-accent disabled:opacity-50"
      >
        <SwitchPrimitive.Thumb className="block h-3.5 w-3.5 translate-x-0.5 rounded-full bg-white transition-transform data-[state=checked]:translate-x-[16px]" />
      </SwitchPrimitive.Root>
      {label}
    </label>
  );
}

export interface SelectOption<T extends string> {
  value: T;
  label: ReactNode;
  disabled?: boolean;
}

export function Select<T extends string>({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  className,
  "aria-label": ariaLabel,
}: {
  value: T | null;
  onChange(v: T): void;
  options: SelectOption<T>[];
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <SelectPrimitive.Root value={value ?? undefined} onValueChange={(v) => onChange(v as T)} disabled={disabled}>
      <SelectPrimitive.Trigger
        aria-label={ariaLabel}
        className={cx(
          "inline-flex h-8 min-w-[10rem] items-center justify-between gap-2 rounded border border-border bg-bg px-2.5 text-base text-fg disabled:text-faint",
          className,
        )}
      >
        <SelectPrimitive.Value placeholder={<span className="text-faint">{placeholder}</span>} />
        <SelectPrimitive.Icon>
          <ChevronDown size={14} className="text-muted" />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={4}
          className="z-50 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded border border-border bg-panel-2 shadow-xl"
        >
          <SelectPrimitive.Viewport className="p-1">
            {options.map((o) => (
              <SelectPrimitive.Item
                key={o.value}
                value={o.value}
                disabled={o.disabled}
                className="relative flex cursor-pointer select-none items-center rounded-sm py-1.5 pl-6 pr-3 text-base text-fg outline-none data-[disabled]:cursor-not-allowed data-[highlighted]:bg-accent data-[highlighted]:text-accent-fg data-[disabled]:text-faint"
              >
                <SelectPrimitive.ItemIndicator className="absolute left-1.5">
                  <Check size={12} />
                </SelectPrimitive.ItemIndicator>
                <SelectPrimitive.ItemText>{o.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}
