import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { MetaProvider } from "@/api/MetaContext";
import { PrefsProvider } from "@/hooks/usePrefs";

export interface RenderAppOptions extends Omit<RenderOptions, "wrapper"> {
  /** The route to start on, for tests that care which section is open. */
  route?: string;
  /** Skip the vocabulary provider, for a component that does not read it. */
  withMeta?: boolean;
}

/**
 * Renders a component the way the app does: inside a router, the vocabulary
 * provider and the preferences provider, so a test never has to stub context by
 * hand — and so a component reading a stored preference is exercised through
 * the same path the app uses.
 */
export function renderApp(ui: ReactElement, options: RenderAppOptions = {}) {
  const { route = "/map", withMeta = true, ...rest } = options;
  const Wrapper = ({ children }: { children: ReactNode }) => {
    const inner = withMeta ? <MetaProvider>{children}</MetaProvider> : children;
    return (
      <MemoryRouter initialEntries={[route]}>
        <PrefsProvider>{inner}</PrefsProvider>
      </MemoryRouter>
    );
  };
  return { user: userEvent.setup(), ...render(ui, { wrapper: Wrapper, ...rest }) };
}

export { userEvent };
