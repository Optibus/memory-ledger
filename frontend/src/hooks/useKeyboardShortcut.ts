import { useEffect } from "react";

/**
 * Binds a bare key, and stays out of the way while the person is typing.
 *
 * `m` opening the drawer mid-word in the search box was the old page's most
 * reported annoyance.
 */
export function useKeyboardShortcut(key: string, handler: (e: KeyboardEvent) => void): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key.toLowerCase() !== key.toLowerCase()) return;
      handler(e);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [key, handler]);
}
