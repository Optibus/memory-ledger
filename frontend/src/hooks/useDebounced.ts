import { useEffect, useState } from "react";

/** Holds a value still until typing stops — the search box and link recognition. */
export function useDebounced<T>(value: T, delayMs = 250): T {
  const [held, setHeld] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setHeld(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return held;
}
