import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { PeopleSection } from "./PeopleSection";
import { renderApp } from "@/test/utils";
import { resetWrites, server, writes } from "@/test/server";
import { personDetailFixture, personDuplicatesFixture } from "@/test/fixtures";

beforeEach(resetWrites);

describe("People", () => {
  it("lists everyone the brain knows, with the names they also answer to", async () => {
    renderApp(<PeopleSection />, { route: "/people" });
    // Scoped to the list: the same names also appear in the duplicates card,
    // which is a different claim about them.
    const list = await screen.findByRole("list", { name: "people" });
    expect(within(list).getByText("Gabriel")).toBeInTheDocument();
    expect(within(list).getByText("Isadora")).toBeInTheDocument();
    expect(within(list).getByText("also isa")).toBeInTheDocument();
  });

  it("searches aliases too, because that is the point of having them", async () => {
    const { user } = renderApp(<PeopleSection />, { route: "/people" });
    const list = await screen.findByRole("list", { name: "people" });
    await user.type(screen.getByLabelText("filter people"), "isa");
    expect(within(list).getByText("Isadora")).toBeInTheDocument();
    expect(within(list).queryByText("Gabriel")).not.toBeInTheDocument();
  });

  it("fetches a person's connections only when you open them", async () => {
    let asked = 0;
    server.use(
      http.get("/api/node", () => {
        asked += 1;
        return HttpResponse.json(personDetailFixture);
      }),
    );
    const { user } = renderApp(<PeopleSection />, { route: "/people" });
    const list = await screen.findByRole("list", { name: "people" });
    expect(asked).toBe(0);              // a hundred people is not a hundred reads

    await user.click(within(list).getByRole("button", { name: /Gabriel/ }));
    await waitFor(() => expect(asked).toBe(1));
  });

  it("groups a person's connections by relation", async () => {
    const { user } = renderApp(<PeopleSection />, { route: "/people" });
    const list = await screen.findByRole("list", { name: "people" });
    await user.click(within(list).getByRole("button", { name: /Gabriel/ }));
    // Each neighbour, under the heading for the relation that reaches it, with
    // the biggest group first — two tasks before the one note.
    for (const edge of personDetailFixture.edges) {
      expect(await within(list).findByText(edge.title)).toBeInTheDocument();
    }
    const headings = within(list)
      .getAllByRole("heading", { level: 4 })
      .map((h) => h.textContent);
    expect(headings[0]).toMatch(/Assigned to\s*2/);
    expect(headings[1]).toMatch(/Mentions\s*1/);
  });

  it("pins another spelling through the engine's own add-alias", async () => {
    const { user } = renderApp(<PeopleSection />, { route: "/people" });
    const list = await screen.findByRole("list", { name: "people" });
    await user.click(within(list).getByRole("button", { name: /Gabriel/ }));
    const input = await screen.findByLabelText("Also known as");
    await user.type(input, "Gab");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/add-alias")?.body).toMatchObject({
        id: "person_gabriel",
        alias: "Gab",
      }),
    );
  });
});

describe("People — duplicates", () => {
  it("shows the groups that look like one human, and which node would be kept", async () => {
    renderApp(<PeopleSection />, { route: "/people" });
    expect(await screen.findByText("Possible duplicates")).toBeInTheDocument();
    expect(screen.getByText("keep")).toBeInTheDocument();
    expect(screen.getByText("likely")).toBeInTheDocument();
    // The canonical is the most connected node, so the counts are shown.
    expect(screen.getByText("9 connections")).toBeInTheDocument();
  });

  it("merges into the canonical, and says nothing is deleted before it does", async () => {
    const { user } = renderApp(<PeopleSection />, { route: "/people" });
    await screen.findByText("Possible duplicates");

    await user.click(screen.getByRole("button", { name: "Merge into Isadora" }));
    // The dialog explains the merge before anything is written.
    expect(await screen.findByText(/Nothing is deleted/)).toBeInTheDocument();
    expect(writes.some((w) => w.path === "/api/merge-person")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Merge Isa into Isadora" }));
    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/merge-person")?.body).toMatchObject({
        canonical: "person_isadora",
        duplicate: "person_isa",
      }),
    );
  });

  it("lets a human overrule which spelling is the real person", async () => {
    const { user } = renderApp(<PeopleSection />, { route: "/people" });
    await screen.findByText("Possible duplicates");

    // The engine picked Isadora (most connected). Say it is the other one.
    const group = screen.getByText("Isa").closest(".ml-dupe") as HTMLElement;
    await user.click(within(group).getByRole("radio", { name: "Isa" }));
    await user.click(within(group).getByRole("button", { name: "Merge into Isa" }));
    await user.click(await screen.findByRole("button", { name: "Merge Isadora into Isa" }));

    await waitFor(() =>
      expect(writes.find((w) => w.path === "/api/merge-person")?.body).toMatchObject({
        canonical: "person_isa",
        duplicate: "person_isadora",
      }),
    );
  });

  it("says so plainly when there are none", async () => {
    server.use(
      http.get("/api/person-duplicates", () =>
        HttpResponse.json({ ...personDuplicatesFixture, duplicates: [] }),
      ),
    );
    renderApp(<PeopleSection />, { route: "/people" });
    expect(await screen.findByText("none")).toBeInTheDocument();
  });
});
