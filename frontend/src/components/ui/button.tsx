import { forwardRef } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost";
type Size = "sm" | "md" | "lg";

const variantClasses: Record<Variant, string> = {
  primary:
    "border-transparent bg-gradient-to-r from-[#365DFF] to-[#7C3AED] text-ink-inverse shadow-[0_10px_24px_rgba(54,93,255,0.24)] hover:from-[#2447DB] hover:to-[#6D28D9] hover:shadow-[0_14px_30px_rgba(54,93,255,0.30)] active:from-[#2447DB] active:to-[#5B21B6]",
  secondary:
    "border-border-subtle bg-white text-[#2447DB] shadow-sm hover:border-[#365DFF]/35 hover:bg-[#EEF3FF]",
  ghost:
    "border-transparent bg-transparent text-ink-secondary hover:bg-[#EEF3FF] hover:text-[#2447DB]",
  danger:
    "border-transparent bg-gradient-to-r from-[#E11D48] to-[#F97316] text-ink-inverse shadow-[0_10px_24px_rgba(225,29,72,0.18)] hover:brightness-95",
  "danger-ghost":
    "border-transparent bg-transparent text-status-danger hover:bg-[#FFF0F4]",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-8 px-3 text-xs gap-1.5",
  md: "h-[38px] px-3.5 text-[13px] gap-1.5",
  lg: "h-11 px-[18px] text-sm gap-2",
};

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  iconOnly?: boolean;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = "secondary",
      size = "md",
      loading = false,
      iconOnly = false,
      className = "",
      disabled,
      children,
      ...props
    },
    ref
  ) => {
    const isDisabled = disabled || loading;
    return (
      <button
        ref={ref}
        disabled={isDisabled}
        className={[
          "inline-flex cursor-pointer items-center justify-center rounded-lg border font-semibold transition-[background,border-color,color,box-shadow,filter] duration-200",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-border-focus focus-visible:ring-offset-2",
          "disabled:cursor-not-allowed disabled:opacity-40",
          iconOnly ? "aspect-square p-0" : "",
          variantClasses[variant],
          sizeClasses[size],
          className,
        ]
          .filter(Boolean)
          .join(" ")}
        {...props}
      >
        {loading && (
          <svg
            className="animate-spin"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
        )}
        {children}
      </button>
    );
  }
);

Button.displayName = "Button";
