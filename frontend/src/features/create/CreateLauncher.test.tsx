import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { CreateLauncher } from "./CreateLauncher";
import { renderApp } from "@/test/utils";
import { failWith, resetWrites, server, writes } from "@/test/server";
import { metaFixture } from "@/test/fixtures";

beforeEach(resetWrites);

const open = async (which: "skill" | "rule") => {
  const { user } = renderApp(<CreateLauncher onCreated={() => {}} />, { route: "/skills" });
  await user.click(await screen.findByRole("button", { name: /create a process, a skill or a rule/ }));
  await user.click(await screen.findByRole("button", { name: which === "skill" ? /A skill/ : /A rule/ }));
  return user;
};

describe("authoring a rule", () => {
  it("builds the form from the shape the server reports, not from headings in the page", async () => {
    await open("rule");
    for (const s of metaFixture.rule_sections) {
      const label = s.heading.replace(/^#+\s*/, "");
      expect(await screen.findByLabelText(new RegExp(label, "i"))).toBeInTheDocument();
    }
  });

  it("refuses a contract that cannot be checked, before the round trip", async () => {
    const user = await open("rule");
    await user.type(await screen.findByLabelText(/^Title/), "Ship the link");
    await user.type(screen.getByLabelText(/Governs/), "sharing a write-up");
    await user.type(screen.getByLabelText(/THE RULE/i), "Post the link.");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    // THE RULE is filled, so the refusal names the section that is still missing.
    expect(await screen.findByText(/JUDGED AGAINST is required/)).toBeInTheDocument();
    expect(writes.filter((w) => w.path === "/api/create-rule")).toHaveLength(0);
  });

  it("says a rule with no cue never fires", async () => {
    const user = await open("rule");
    await user.type(await screen.findByLabelText(/^Title/), "Ship the link");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    expect(await screen.findByText(/a rule with no cue never fires/)).toBeInTheDocument();
  });

  it("writes a complete contract", async () => {
    const user = await open("rule");
    await user.type(await screen.findByLabelText(/^Title/), "Ship the link");
    await user.type(screen.getByLabelText(/Governs/), "sharing a write-up");
    await user.type(screen.getByLabelText(/THE RULE/i), "Post the link, never the scrollback.");
    await user.type(screen.getByLabelText(/JUDGED AGAINST/i), "The message carries a URL.");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/create-rule")?.body).toMatchObject({
        title: "Ship the link",
        scenario: "sharing a write-up",
        sections: {
          the_rule: "Post the link, never the scrollback.",
          judged_against: "The message carries a URL.",
        },
      }),
    );
  });

  it("offers only the sources the engine knows", async () => {
    await open("rule");
    const select = (await screen.findByLabelText(/^Source/)) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(metaFixture.rule_sources);
  });

  it("reports the server's refusal rather than claiming success", async () => {
    failWith("post", "/api/create-rule", 400, "a rule with that id already exists");
    const user = await open("rule");
    await user.type(await screen.findByLabelText(/^Title/), "Ship the link");
    await user.type(screen.getByLabelText(/Governs/), "sharing");
    await user.type(screen.getByLabelText(/THE RULE/i), "x");
    await user.type(screen.getByLabelText(/JUDGED AGAINST/i), "y");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    expect(await screen.findByText("a rule with that id already exists")).toBeInTheDocument();
  });
});

describe("authoring a skill", () => {
  it("leaves out the two sections nobody types", async () => {
    await open("skill");
    expect(await screen.findByLabelText(/When to use/)).toBeInTheDocument();
    // `Rules honored` and `Depends on` are generated from the declarations.
    expect(screen.queryByLabelText(/Rules honored/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^Depends on$/)).not.toBeInTheDocument();
  });

  it("derives the slug from the title until the person edits it", async () => {
    const user = await open("skill");
    await user.type(await screen.findByLabelText(/^Title/), "Weekly Digest");
    expect(screen.getByLabelText(/Name \(slug\)/)).toHaveValue("weekly-digest");
    await user.clear(screen.getByLabelText(/Name \(slug\)/));
    await user.type(screen.getByLabelText(/Name \(slug\)/), "digest");
    await user.type(screen.getByLabelText(/^Title/), " v2");
    expect(screen.getByLabelText(/Name \(slug\)/)).toHaveValue("digest");
  });

  it("refuses an incomplete contract at write time", async () => {
    const user = await open("skill");
    await user.type(await screen.findByLabelText(/^Title/), "Weekly digest");
    await user.click(screen.getByRole("button", { name: "Create skill" }));
    expect(await screen.findByText(/When to use is required/)).toBeInTheDocument();
    expect(writes.filter((w) => w.path === "/api/create-skill")).toHaveLength(0);
  });

  it("says silence is not a declaration", async () => {
    await open("skill");
    expect(await screen.findByText(/silence is not a declaration/)).toBeInTheDocument();
  });

  it("opens a rule form on top, and returns to the skill after writing it", async () => {
    const user = await open("skill");
    await user.click(await screen.findByRole("button", { name: /New rule/ }));
    expect(await screen.findByRole("dialog", { name: "New rule" })).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Title/), "A new rule");
    await user.type(screen.getByLabelText(/Governs/), "some cue");
    await user.type(screen.getByLabelText(/THE RULE/i), "x");
    await user.type(screen.getByLabelText(/JUDGED AGAINST/i), "y");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    // Back in the skill form, which was never closed underneath.
    expect(await screen.findByRole("dialog", { name: "New skill" })).toBeInTheDocument();
  });

  it("writes the declarations it was given", async () => {
    const user = await open("skill");
    await user.type(await screen.findByLabelText(/^Title/), "Weekly digest");
    for (const s of metaFixture.skill_sections.filter((x) => x.required && !x.generated)) {
      await user.type(screen.getByLabelText(new RegExp(s.heading.replace(/^#+\s*/, ""), "i")), "something");
    }
    await user.click(screen.getByRole("button", { name: "Create skill" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/create-skill")?.body).toMatchObject({
        name: "weekly-digest",
        title: "Weekly digest",
      }),
    );
  });
});

describe("the create button on a read-only server", () => {
  it("is not there at all", async () => {
    server.use(http.get("/api/meta", () => HttpResponse.json({ ...metaFixture, writable: false })));
    renderApp(<CreateLauncher onCreated={() => {}} />, { route: "/skills" });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /create a process, a skill or a rule/ })).not.toBeInTheDocument(),
    );
  });
});

describe("recording a process from the + button", () => {
  it("stores it in the realm the brain reports, and says what it wrote", async () => {
    const seen: unknown[] = [];
    const { user } = renderApp(<CreateLauncher onCreated={(c) => seen.push(c)} />, { route: "/" });
    await user.click(await screen.findByRole("button", { name: /create a process, a skill or a rule/ }));
    await user.click(await screen.findByRole("button", { name: /A process/ }));
    const realm = await screen.findByRole("combobox", { name: /Realm/ });
    await waitFor(() => expect(realm).toHaveValue("optibus"));
    expect(realm).toHaveTextContent("optibus (current)");
    await user.type(screen.getByRole("textbox", { name: /Title/ }), "Ship a hotfix");
    await user.click(screen.getByRole("button", { name: "Save process" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/create-process")?.body).toMatchObject({
        title: "Ship a hotfix",
        realm: "optibus",
      }),
    );
    expect(seen).toEqual([{ kind: "process", id: "process_new_0001", realm: "optibus" }]);
  });
});
