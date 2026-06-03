"use client";

import { useState, useEffect } from "react";

/**
 * Viewport detection hook. Returns `null` until mounted (SSR-safe), then a
 * boolean tracking whether the viewport is at or below `breakpoint` px wide.
 * Used by: AppShell (desktop vs mobile shell branch).
 */
export function useIsMobile(breakpoint = 768): boolean | null {
  const [isMobile, setIsMobile] = useState<boolean | null>(null);

  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const update = () => setIsMobile(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, [breakpoint]);

  return isMobile;
}
