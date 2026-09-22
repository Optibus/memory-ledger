import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { ApiError, api, get, post, queryString } from "./client";
import { server } from "@/test/server";

describe("queryString", () => {
  it("drops empty, null and undefined params rather than sending them blank", () => {
    expect(queryString({ a: "1", b: "", c: undefined, d: null, e: 0 })).toBe("?a=1&e=0");
  });
  it("is empty when there is nothing to ask", () => {
    expect(queryString()).toBe("");
    expect(queryString({})).toBe("");
  });
  it("encodes a value that would otherwise break the URL", () => {
    expect(queryString({ q: "a & b" })).toBe("?q=a+%26+b");
  });
});

describe("the envelope", () => {
  it("turns ok:false into the server's own reason, not a status code", async () => {
    server.use(
      http.post("/api/set-status", () =>
        HttpResponse.json({ ok: false, error: "status must be one of open,doing,done" }, { status: 400 }),
      ),
    );
    await expect(api.setStatus("task_1", "nope")).rejects.toThrow(
      "status must be one of open,doing,done",
    );
  });

  it("carries the HTTP status on the error", async () => {
    server.use(http.get("/api/graph", () => HttpResponse.json({ ok: false, error: "boom" }, { status: 500 })));
    await expect(api.graph()).rejects.toMatchObject({ status: 500, name: "ApiError" });
  });

  it("reports a 403 from --no-write as the server worded it", async () => {
    server.use(
      http.post("/api/complete", () =>
        HttpResponse.json(
          { ok: false, error: "server started with --no-write; writes are disabled" },
          { status: 403 },
        ),
      ),
    );
    await expect(api.complete("task_1")).rejects.toThrow(/--no-write/);
  });

  it("does not mistake a body that is unreadable for a success", async () => {
    server.use(http.get("/api/status", () => new HttpResponse("not json", { status: 200 })));
    await expect(api.status()).rejects.toBeInstanceOf(ApiError);
  });

  it("returns the payload untouched when the call worked", async () => {
    const meta = await api.meta();
    expect(meta.writable).toBe(true);
    expect(meta.statuses).toContain("blocked");
  });
});

describe("the named surface", () => {
  it("sends reads as GET with the params the server documents", async () => {
    let seen = "";
    server.use(
      http.get("/api/agenda", ({ request }) => {
        seen = new URL(request.url).search;
        return HttpResponse.json({ ok: true, view: "agenda", title: "Agenda", count: 0, results: [] });
      }),
    );
    await api.view("agenda", { mine: "1", realm: "optibus,shared" });
    expect(seen).toBe("?mine=1&realm=optibus%2Cshared");
  });

  it("sends writes as JSON POST", async () => {
    let body: unknown;
    server.use(
      http.post("/api/set-due", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true });
      }),
    );
    await api.setDue("task_1", "2026-09-30T12:00:00");
    expect(body).toEqual({ id: "task_1", due: "2026-09-30T12:00:00" });
  });

  it("builds a same-origin bundle download URL", () => {
    expect(api.bundleUrl("b.zip")).toBe("/api/bundle?name=b.zip");
  });

  it("passes an abort signal through so an unmount cancels the request", async () => {
    const ctrl = new AbortController();
    const p = get("/api/graph", undefined, ctrl.signal);
    ctrl.abort();
    await expect(p).rejects.toThrow();
  });

  it("posts an empty object when a write has no body of its own", async () => {
    let body: unknown;
    server.use(
      http.post("/api/flush", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ok: true });
      }),
    );
    await post("/api/flush");
    expect(body).toEqual({});
  });
});
