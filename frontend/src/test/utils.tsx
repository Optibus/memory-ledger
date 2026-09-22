import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import type { RenderOptions } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { MetaProvider } from "@/api/MetaContext";

export interface RenderAppOptions extends Omit<RenderOptions, "wrapper"> {
  /** The route to start on, for tests that care which section is open. */
  route?: string;
  /** Skip the vocabulary provider, for a component that does not read it. */
  withMeta?: boolean;
}

/**
 * Renders a component the way the app does: inside a router and the vocabulary
 * provider, so a test never has to stub context by hand.
 */
export function renderApp(ui: ReactElement, options: RenderAppOptions = {}) {
  const { route = "/map", withMeta = true, ...rest } = options;
  const Wrapper = ({ children }: { children: ReactNode }) => {
    const inner = withMeta ? <MetaProvider>{children}</MetaProvider> : children;
    return <MemoryRouter initialEntries={[route]}>{inner}</MemoryRouter>;
  };
  return { user: userEvent.setup(), ...render(ui, { wrapper: Wrapper, ...rest }) };
}

export { userEvent };
