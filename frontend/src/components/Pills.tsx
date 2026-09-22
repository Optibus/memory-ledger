import { Button } from "./Button";
import "./Pills.css";

export interface PillOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

export interface PillsProps<T extends string> {
  options: PillOption<T>[];
  value: T;
  onChange: (value: T) => void;
  "aria-label"?: string;
}

/** A segmented single-choice control — the skills filters, the dashboard tabs. */
export function Pills<T extends string>({ options, value, onChange, ...rest }: PillsProps<T>) {
  return (
    <div className="ml-pills" role="group" aria-label={rest["aria-label"]}>
      {options.map((o) => (
        <Button
          key={o.value}
          size="sm"
          active={o.value === value}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </Button>
      ))}
    </div>
  );
}
