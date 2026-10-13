import { describe, expect, it } from 'vitest';
import { PlayerClock } from '../src/clock';

describe('PlayerClock', () => {
  it('ignores one late message, follows a real jump', () => {
    const c = new PlayerClock();
    for (let t = 0; t < 5; t += 1) c.add(10_000 + t * 1000, t * 1000); // offset 10 000
    expect(c.offset).toBe(10_000);
    c.add(13_000, 5000); // a message 2 s late: one sample, ignored
    expect(c.offset).toBe(10_000);
    c.add(16_000, 6000);
    expect(c.offset).toBe(10_000);
    c.add(3_607_000, 7000); // the server's clock really is an hour on…
    c.add(3_608_000, 8000); // …and the next message agrees
    expect(c.offset).toBe(3_600_000);
  });
});
