import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

/**
 * Subscribes to the backend Server-Sent Events stream and invalidates the
 * relevant queries (batched) so every screen reflects real workflow state
 * without manual refreshes.
 */
export function useLive(): { connected: boolean } {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const pending = useRef<Set<string>>(new Set());
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: number | null = null;

    const flush = () => {
      const keys = Array.from(pending.current);
      pending.current.clear();
      timer.current = null;
      keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    };
    const mark = (...keys: string[]) => {
      keys.forEach((k) => pending.current.add(k));
      if (timer.current === null) timer.current = window.setTimeout(flush, 250);
    };

    const connect = () => {
      es = new EventSource("/api/events");
      es.onopen = () => {
        setConnected(true);
        mark("status", "stats", "queue", "products", "product", "audit", "review", "completed");
      };
      es.onerror = () => {
        setConnected(false);
        es?.close();
        retry = window.setTimeout(connect, 3000);
      };
      es.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as { type: string };
          switch (msg.type) {
            case "audit":
              mark("audit", "product", "queue");
              break;
            case "product":
              mark("products", "product", "queue", "stats", "review", "completed", "status");
              break;
            case "inbox":
            case "intake":
              mark("queue", "status");
              break;
            case "system":
              mark("status", "queue", "settings", "product", "references");
              break;
            case "dry_run":
              mark("dryrun");
              break;
            default:
              mark("status");
          }
        } catch {
          /* ignore keep-alives */
        }
      };
    };
    connect();
    return () => {
      es?.close();
      if (retry) window.clearTimeout(retry);
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [qc]);

  return { connected };
}
