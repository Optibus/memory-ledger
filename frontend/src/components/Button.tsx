import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./Button.css";

export type ButtonVariant = "default" | "primary" | "ghost" | "danger" | "nav";
export type ButtonSize = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Renders the pressed/selected look and reports it to assistive tech. */
  active?: boolean;
  icon?: ReactNode;
}

/**
 * The only button in the app.
 *
 * The old page had eleven button styles that had drifted apart (`.dbtn`,
 * `.ddb`, `#side button`, `.pills button`, …); every one of them is a variant
 * here, so "what does a button look like" has one answer.
 */
export function Button({
  variant = "default",
  size = "md",
  active = false,
  icon,
  className = "",
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  const classes = [
    "ml-btn",
    `ml-btn--${variant}`,
    `ml-btn--${size}`,
    active ? "is-active" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type} className={classes} aria-pressed={active || undefined} {...rest}>
      {icon ? <span className="ml-btn__icon" aria-hidden="true">{icon}</span> : null}
      {children}
    </button>
  );
}
