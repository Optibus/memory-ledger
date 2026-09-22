import type { HTMLAttributes, ReactNode } from "react";
import "./Card.css";

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  /** Adds the hover affordance for a card you can open. */
  interactive?: boolean;
  selected?: boolean;
}

/** The list surface — a task row, a skill, a rule, a settings group. */
export function Card({ children, interactive, selected, className = "", ...rest }: CardProps) {
  return (
    <div
      className={["ml-card", interactive ? "is-interactive" : "", selected ? "is-selected" : "", className]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = "", ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`ml-card__head ${className}`.trim()} {...rest}>{children}</div>;
}

export function CardBody({ children, className = "", ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`ml-card__body ${className}`.trim()} {...rest}>{children}</div>;
}

export function CardActions({ children, className = "", ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`ml-card__actions ${className}`.trim()} {...rest}>{children}</div>;
}
