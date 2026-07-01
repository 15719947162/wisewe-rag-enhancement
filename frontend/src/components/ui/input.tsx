import { forwardRef } from "react";

type InputProps = React.InputHTMLAttributes<HTMLInputElement> & {
  error?: boolean;
  prefixIcon?: React.ReactNode;
  suffixIcon?: React.ReactNode;
  inputSize?: "sm" | "md";
};

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    {
      error = false,
      prefixIcon,
      suffixIcon,
      inputSize = "md",
      className = "",
      disabled,
      ...props
    },
    ref
  ) => {
    const heightClass = inputSize === "sm" ? "h-8" : "h-9";
    return (
      <div className="relative flex items-center">
        {prefixIcon && (
          <span className="pointer-events-none absolute left-3 text-ink-tertiary">
            {prefixIcon}
          </span>
        )}
        <input
          ref={ref}
          disabled={disabled}
          className={[
            "w-full rounded-md border bg-white text-[13px] text-ink-primary placeholder:text-ink-tertiary shadow-sm",
            "transition-colors duration-150",
            "focus:outline-none focus:ring-2 focus:ring-border-focus focus:ring-offset-0 focus:border-border-focus",
            "disabled:cursor-not-allowed disabled:bg-subtle disabled:opacity-60",
            heightClass,
            prefixIcon ? "pl-9" : "pl-3",
            suffixIcon ? "pr-9" : "pr-3",
            error
              ? "border-status-danger bg-[#FFF0F4]"
              : "border-border-subtle hover:border-[#365DFF]/45",
            className,
          ]
            .filter(Boolean)
            .join(" ")}
          {...props}
        />
        {suffixIcon && (
          <span className="pointer-events-none absolute right-3 text-ink-tertiary">
            {suffixIcon}
          </span>
        )}
      </div>
    );
  }
);

Input.displayName = "Input";
