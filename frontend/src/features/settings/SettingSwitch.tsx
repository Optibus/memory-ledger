import { Badge, Checkbox } from "@/components";
import type { SettingSource } from "@/api/types";
import "./SettingsSection.css";

export interface SettingSwitchProps {
  label: string;
  hint: string;
  checked: boolean;
  source: SettingSource;
  /** The env var that would take this over, named so the badge can say which. */
  envVar: string;
  disabled?: boolean;
  onChange: (v: boolean) => void;
}

/**
 * One boolean setting, with where its value came from.
 *
 * The badge is not decoration. These switches are overridden by a NEGATIVE env
 * var ($MEMORY_LEDGER_NO_AUTOSYNC), so a shell that exports one leaves this box
 * unticked and unable to do anything about it. Saying "from the environment"
 * out loud is the difference between a switch and a lie.
 */
export function SettingSwitch({
  label,
  hint,
  checked,
  source,
  envVar,
  disabled,
  onChange,
}: SettingSwitchProps) {
  const overridden = source === "env";
  return (
    <div className="ml-settings__switch">
      <Checkbox
        checked={checked}
        disabled={disabled || overridden}
        onChange={onChange}
        label={
          <>
            {label}{" "}
            {overridden ? (
              <Badge tone="warn">${envVar}</Badge>
            ) : source === "settings" ? (
              <Badge tone="neutral">saved</Badge>
            ) : (
              <Badge tone="neutral">default</Badge>
            )}
          </>
        }
      />
      <p className="ml-settings__hint ml-settings__hint--switch">
        {hint}
        {overridden ? (
          <>
            {" "}
            <strong>
              ${envVar} is set in the server&rsquo;s environment and outranks this file — unset it
              there to get the switch back.
            </strong>
          </>
        ) : null}
      </p>
    </div>
  );
}
