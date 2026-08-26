import { useCallback, useRef } from "react";

/**
 * Guards against out-of-order async responses: if `refresh()` is called
 * again (e.g. a mutation re-fetching) before an earlier call's response
 * arrives, the earlier response is discarded instead of clobbering newer
 * state. Without this, a slow initial-mount fetch that resolves *after* a
 * later refresh can silently overwrite the page with stale data — e.g. an
 * item just created disappearing from the list it was added to.
 */
export function useLatestGuard() {
  const idRef = useRef(0);
  return useCallback(async function guard<T>(run: () => Promise<T>, apply: (result: T) => void) {
    const id = ++idRef.current;
    const result = await run();
    if (id === idRef.current) apply(result);
  }, []);
}
