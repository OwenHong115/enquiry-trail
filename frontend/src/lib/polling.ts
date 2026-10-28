import { useEffect, useRef } from "react";

export function shouldPoll(visibilityState: string): boolean {
  return visibilityState === "visible";
}

/** Poll fn every intervalMs, but only while the page is visible (A28). */
export function usePolling(fn: () => void | Promise<void>, intervalMs: number, enabled = true): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let timer: number | undefined;
    const tick = () => { void ref.current(); };
    const schedule = () => {
      stop();
      if (shouldPoll(document.visibilityState)) {
        timer = window.setInterval(tick, intervalMs);
      }
    };
    const stop = () => { if (timer !== undefined) window.clearInterval(timer); timer = undefined; };
    const onVisibility = () => {
      if (shouldPoll(document.visibilityState)) { tick(); schedule(); } else { stop(); }
    };
    schedule();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [intervalMs, enabled]);
}
