import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useAsync } from "./useAsync";
import { usePolling } from "./usePolling";
import { useDebounced } from "./useDebounced";
import { useKeyboardShortcut } from "./useKeyboardShortcut";

describe("useAsync", () => {
  it("reports loading, then the value", async () => {
    const { result } = renderHook(() => useAsync(async () => "hello", []));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.data).toBe("hello"));
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("surfaces the server's own reason on failure", async () => {
    const { result } = renderHook(() => useAsync(async () => Promise.reject(new Error("no node x")), []));
    await waitFor(() => expect(result.current.error).toBe("no node x"));
  });

  it("does not report an abort as a failure", async () => {
    const { result, unmount } = renderHook(() =>
      useAsync(
        () => Promise.reject(new DOMException("aborted", "AbortError")),
        [],
      ),
    );
    unmount();
    expect(result.current.error).toBeNull();
  });

  it("re-runs on reload", async () => {
    let n = 0;
    const { result } = renderHook(() => useAsync(async () => ++n, []));
    await waitFor(() => expect(result.current.data).toBe(1));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.data).toBe(2));
  });
});

describe("usePolling", () => {
  it("keeps only one request in flight at a time", async () => {
    vi.useFakeTimers();
    let inFlight = 0;
    let peak = 0;
    let done = 0;
    const fn = vi.fn(async () => {
      inFlight++;
      peak = Math.max(peak, inFlight);
      await Promise.resolve();
      inFlight--;
      return ++done;
    });

    const { unmount } = renderHook(() => usePolling(fn, { intervalMs: 10 }));
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10);
      });
    }
    expect(peak).toBe(1);
    expect(done).toBeGreaterThan(1);
    unmount();
    vi.useRealTimers();
  });

  it("goes not-live on a failure and live again when it recovers", async () => {
    let fail = true;
    const { result } = renderHook(() =>
      usePolling(async () => {
        if (fail) throw new Error("can't reach server");
        return "ok";
      }, { intervalMs: 5 }),
    );
    await waitFor(() => expect(result.current.error).toBe("can't reach server"));
    expect(result.current.live).toBe(false);
    fail = false;
    await waitFor(() => expect(result.current.live).toBe(true));
    expect(result.current.error).toBeNull();
  });

  it("re-asks at once when the request identity changes", async () => {
    const seen: string[] = [];
    const { rerender } = renderHook(
      ({ k }) =>
        usePolling(
          async () => {
            seen.push(k);
            return k;
          },
          // A long interval on purpose: without the key this would not re-ask
          // until the next tick, which is the bug the key exists to prevent.
          { intervalMs: 60_000, key: k },
        ),
      { initialProps: { k: "agenda" } },
    );
    await waitFor(() => expect(seen).toEqual(["agenda"]));
    rerender({ k: "week" });
    await waitFor(() => expect(seen).toEqual(["agenda", "week"]));
  });

  it("asks nothing at all while disabled", async () => {
    const fn = vi.fn(async () => 1);
    renderHook(() => usePolling(fn, { intervalMs: 5, enabled: false }));
    await new Promise((r) => setTimeout(r, 30));
    expect(fn).not.toHaveBeenCalled();
  });

  it("stops polling once unmounted", async () => {
    const fn = vi.fn(async () => 1);
    const { unmount } = renderHook(() => usePolling(fn, { intervalMs: 5 }));
    await waitFor(() => expect(fn).toHaveBeenCalled());
    unmount();
    const after = fn.mock.calls.length;
    await new Promise((r) => setTimeout(r, 30));
    expect(fn.mock.calls.length).toBe(after);
  });
});

describe("useDebounced", () => {
  it("holds the value still until typing stops", async () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ v }) => useDebounced(v, 200), {
      initialProps: { v: "a" },
    });
    rerender({ v: "ab" });
    rerender({ v: "abc" });
    expect(result.current).toBe("a");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(result.current).toBe("abc");
    vi.useRealTimers();
  });
});

describe("useKeyboardShortcut", () => {
  it("fires on the bare key", () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcut("m", handler));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "m" }));
    expect(handler).toHaveBeenCalledOnce();
  });

  it("stays out of the way while the person is typing", () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcut("m", handler));
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "m", bubbles: true }));
    expect(handler).not.toHaveBeenCalled();
    input.remove();
  });

  it("ignores the key when a modifier is held, so it never steals a browser shortcut", () => {
    const handler = vi.fn();
    renderHook(() => useKeyboardShortcut("m", handler));
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "m", metaKey: true }));
    expect(handler).not.toHaveBeenCalled();
  });

  it("unbinds on unmount", () => {
    const handler = vi.fn();
    const { unmount } = renderHook(() => useKeyboardShortcut("m", handler));
    unmount();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "m" }));
    expect(handler).not.toHaveBeenCalled();
  });
});
