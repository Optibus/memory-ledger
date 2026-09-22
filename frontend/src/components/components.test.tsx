import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import {
  Badge,
  Button,
  Checkbox,
  ConfirmDialog,
  Dropdown,
  EmptyState,
  FacetDropdown,
  Field,
  KeyValue,
  Menu,
  Modal,
  Pills,
  SearchInput,
  StatusMessage,
  Tree,
} from "./index";

describe("Button", () => {
  it("reports its pressed state to assistive tech", () => {
    render(<Button active>Filter</Button>);
    expect(screen.getByRole("button", { name: "Filter" })).toHaveAttribute("aria-pressed", "true");
  });

  it("leaves aria-pressed off a button that is not a toggle", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).not.toHaveAttribute("aria-pressed");
  });

  it("defaults to type=button, so it never submits a form by accident", () => {
    render(<Button>Add</Button>);
    expect(screen.getByRole("button", { name: "Add" })).toHaveAttribute("type", "button");
  });

  it("does not fire while disabled", async () => {
    const onClick = vi.fn();
    render(<Button disabled onClick={onClick}>Go</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("Dropdown", () => {
  const open = async () => {
    const user = userEvent.setup();
    render(
      <Dropdown label="Display" summary="default">
        <p>panel contents</p>
      </Dropdown>,
    );
    await user.click(screen.getByRole("button", { name: /Display/ }));
    return user;
  };

  it("starts closed and opens on click", async () => {
    await open();
    expect(screen.getByText("panel contents")).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const user = await open();
    await user.keyboard("{Escape}");
    expect(screen.queryByText("panel contents")).not.toBeInTheDocument();
  });

  it("closes on a click outside itself", async () => {
    const user = await open();
    await user.click(document.body);
    expect(screen.queryByText("panel contents")).not.toBeInTheDocument();
  });

  it("keeps aria-expanded honest", async () => {
    const user = await open();
    const btn = screen.getByRole("button", { name: /Display/ });
    expect(btn).toHaveAttribute("aria-expanded", "true");
    await user.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });
});

describe("FacetDropdown", () => {
  function Harness({ initial = ["task", "person"] }: { initial?: string[] }) {
    const [sel, setSel] = useState(new Set(initial));
    return (
      <FacetDropdown
        name="types"
        options={[
          { value: "task", count: 9 },
          { value: "person", count: 4 },
          { value: "note", count: 6 },
        ]}
        selected={sel}
        onChange={setSel}
      />
    );
  }

  it("says what the closed menu is doing", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: /2\/3/ })).toBeInTheDocument();
  });

  it("says 'all' when nothing is filtered out", () => {
    render(<Harness initial={["task", "person", "note"]} />);
    expect(screen.getByRole("button", { name: /all/ })).toBeInTheDocument();
  });

  it("says 'none' when everything is", () => {
    render(<Harness initial={[]} />);
    expect(screen.getByRole("button", { name: /none/ })).toBeInTheDocument();
  });

  it("shows each option with its count", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /types/ }));
    expect(screen.getByRole("checkbox", { name: /task/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /note/ })).not.toBeChecked();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("toggles one value without touching the others", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /types/ }));
    await user.click(screen.getByRole("checkbox", { name: /note/ }));
    expect(screen.getByRole("checkbox", { name: /note/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /task/ })).toBeChecked();
  });

  it("applies all and none to this facet only", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: /types/ }));
    await user.click(screen.getByRole("button", { name: "none" }));
    expect(screen.getAllByRole("checkbox").every((c) => !(c as HTMLInputElement).checked)).toBe(true);
    await user.click(screen.getByRole("button", { name: "all" }));
    expect(screen.getAllByRole("checkbox").every((c) => (c as HTMLInputElement).checked)).toBe(true);
  });

  it("explains an empty facet instead of showing an empty box", async () => {
    const user = userEvent.setup();
    render(<FacetDropdown name="kinds" options={[]} selected={new Set()} onChange={() => {}} />);
    await user.click(screen.getByRole("button", { name: /kinds/ }));
    expect(screen.getByText(/nothing in the brain carries a kind yet/)).toBeInTheDocument();
  });
});

describe("Modal", () => {
  it("is a labelled dialog", () => {
    render(<Modal title="New rule" onClose={() => {}}>body</Modal>);
    expect(screen.getByRole("dialog", { name: "New rule" })).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Modal title="New rule" onClose={onClose}>body</Modal>);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes on a click on the scrim but not inside the dialog", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Modal title="New rule" onClose={onClose}><p>inside</p></Modal>);
    await user.click(screen.getByText("inside"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("peels the stack one dialog at a time", async () => {
    const outer = vi.fn();
    const inner = vi.fn();
    const user = userEvent.setup();
    render(
      <>
        <Modal title="Skill" onClose={outer}>outer</Modal>
        <Modal title="Rule" depth={1} onClose={inner}>inner</Modal>
      </>,
    );
    await user.keyboard("{Escape}");
    expect(inner).toHaveBeenCalledOnce();
    expect(outer).not.toHaveBeenCalled();
  });
});

describe("SearchInput", () => {
  function Harness() {
    const [v, setV] = useState("");
    return <SearchInput value={v} onChange={setV} placeholder="search nodes…" />;
  }

  it("shows the clear button only once there is something to clear", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    await user.type(screen.getByRole("searchbox"), "ship");
    expect(screen.getByRole("button", { name: /clear/ })).toBeInTheDocument();
  });

  it("clears on the button and on Escape", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole("searchbox");
    await user.type(box, "ship");
    await user.click(screen.getByRole("button", { name: /clear/ }));
    expect(box).toHaveValue("");
    await user.type(box, "again");
    await user.keyboard("{Escape}");
    expect(box).toHaveValue("");
  });
});

describe("Field", () => {
  it("points the label at the control it generated an id for", () => {
    render(
      <Field label="Title" required hint="one line">
        {(id) => <input id={id} />}
      </Field>,
    );
    expect(screen.getByLabelText(/Title/)).toBeInTheDocument();
    expect(screen.getByText("one line")).toBeInTheDocument();
  });

  it("announces an error", () => {
    render(<Field label="Title" error="required">{(id) => <input id={id} />}</Field>);
    expect(screen.getByRole("alert")).toHaveTextContent("required");
  });
});

describe("KeyValue", () => {
  it("drops rows with no value, so a blank field shows nothing", () => {
    render(<KeyValue pairs={[["id", "task_1"], ["due", ""], ["realm", null]]} />);
    expect(screen.getByText("task_1")).toBeInTheDocument();
    expect(screen.queryByText("due")).not.toBeInTheDocument();
    expect(screen.queryByText("realm")).not.toBeInTheDocument();
  });

  it("renders nothing at all when every row is empty", () => {
    const { container } = render(<KeyValue pairs={[["a", ""], ["b", undefined]]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Menu", () => {
  it("groups its items and fires the one that was picked", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <Menu
        label="Options"
        groups={[{ heading: "This brain", items: [{ id: "a", label: "Select", description: "tick things", onSelect }] }]}
      />,
    );
    await user.click(screen.getByRole("button", { name: /Options/ }));
    expect(screen.getByText("This brain")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Select/ }));
    expect(onSelect).toHaveBeenCalledOnce();
  });
});

describe("Pills", () => {
  it("marks exactly one option active", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <Pills
        aria-label="views"
        value="agenda"
        onChange={onChange}
        options={[{ value: "agenda", label: "agenda" }, { value: "week", label: "week" }]}
      />,
    );
    const group = screen.getByRole("group", { name: "views" });
    expect(within(group).getByRole("button", { name: "agenda" })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(group).getByRole("button", { name: "week" }));
    expect(onChange).toHaveBeenCalledWith("week");
  });
});

describe("Tree", () => {
  it("nests children under their parent", () => {
    render(
      <Tree
        nodes={[{ id: "a", kids: [{ id: "b", kids: [] }] }]}
        childrenOf={(n) => n.kids}
        keyOf={(n) => n.id}
        render={(n, depth) => <span>{`${n.id}@${depth}`}</span>}
      />,
    );
    expect(screen.getByText("a@0")).toBeInTheDocument();
    expect(screen.getByText("b@1")).toBeInTheDocument();
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(
      <Tree nodes={[]} childrenOf={() => []} keyOf={(n: { id: string }) => n.id} render={() => null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("feedback components", () => {
  it("StatusMessage renders nothing when there is nothing to say", () => {
    const { container } = render(<StatusMessage>{""}</StatusMessage>);
    expect(container).toBeEmptyDOMElement();
  });

  it("StatusMessage alerts on an error and merely reports otherwise", () => {
    const { rerender } = render(<StatusMessage tone="error">broke</StatusMessage>);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    rerender(<StatusMessage>fine</StatusMessage>);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("EmptyState says why the list is empty", () => {
    render(<EmptyState title="Nothing here." hint="No row matches." />);
    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
    expect(screen.getByText("No row matches.")).toBeInTheDocument();
  });
});

describe("Checkbox and Badge", () => {
  it("Checkbox reports its count and swatch without losing the label", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<Checkbox checked={false} onChange={onChange} label="postmortem" count={3} swatch="#fff" />);
    await user.click(screen.getByRole("checkbox", { name: /postmortem/ }));
    expect(onChange).toHaveBeenCalledWith(true);
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("Badge paints an explicit colour when the type palette gives one", () => {
    render(<Badge tone="type" color="rgb(88, 166, 255)">task</Badge>);
    expect(screen.getByText("task")).toHaveStyle({ background: "rgb(88, 166, 255)" });
  });
});

describe("ConfirmDialog", () => {
  it("asks before it acts, and reports while it is acting", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <ConfirmDialog title="Delete 2 items?" body="cannot be undone" danger onConfirm={onConfirm} onCancel={onCancel} />,
    );
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    rerender(
      <ConfirmDialog title="Delete 2 items?" body="cannot be undone" busy onConfirm={onConfirm} onCancel={onCancel} />,
    );
    expect(screen.getByRole("button", { name: "working…" })).toBeDisabled();
  });
});
