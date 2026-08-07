import { describe, expect, it } from 'vitest';

import { createDeduper } from '../src/mastra/e2a/dedupe';

describe('createDeduper', () => {
  it('claims an id once', () => {
    const d = createDeduper();

    expect(d.claim('evt_1')).toBe(true);
    expect(d.claim('evt_1')).toBe(false);
    expect(d.claim('evt_1')).toBe(false);
  });

  it('treats distinct ids independently', () => {
    const d = createDeduper();

    expect(d.claim('evt_1')).toBe(true);
    expect(d.claim('evt_2')).toBe(true);
    expect(d.size()).toBe(2);
  });

  it('stays bounded by evicting the oldest id', () => {
    const d = createDeduper(3);

    d.claim('a');
    d.claim('b');
    d.claim('c');
    expect(d.size()).toBe(3);

    d.claim('d');
    expect(d.size()).toBe(3);

    // 'a' was evicted, so it is claimable again — the documented trade-off of a
    // bounded in-memory set. A shared unique-insert has no such window.
    expect(d.claim('a')).toBe(true);
    // 'd' is still the most recent and remains claimed.
    expect(d.claim('d')).toBe(false);
  });
});
