/**
 * Webhook delivery is at-least-once: a delivery repeats if your handler is slow,
 * errors, or the connection drops after you did the work. The visible failure is
 * a second reply in someone's inbox, so events are claimed by id *before* the
 * agent runs.
 *
 * This is a bounded in-memory set — correct for a single instance, and the one
 * thing to replace before running more than one. To do that, back `claim` with a
 * unique insert on the event id (any shared table) and return false on conflict.
 */
export interface Deduper {
  /** Returns true the first time an id is seen, false for every repeat. */
  claim(eventId: string): boolean;
  /** Number of ids currently tracked. Exposed for tests and diagnostics. */
  size(): number;
}

export function createDeduper(maxTracked = 5_000): Deduper {
  const seen = new Set<string>();

  return {
    claim(eventId: string): boolean {
      if (seen.has(eventId)) return false;
      if (seen.size >= maxTracked) {
        // Set preserves insertion order, so the first key is the oldest.
        const oldest = seen.values().next().value;
        if (oldest !== undefined) seen.delete(oldest);
      }
      seen.add(eventId);
      return true;
    },
    size: () => seen.size,
  };
}
