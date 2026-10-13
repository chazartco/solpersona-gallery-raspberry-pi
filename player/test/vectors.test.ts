import { describe, expect, it } from 'vitest';
import vectors from '../../docs/solpersona/test-vectors.json';
import { offsetSample } from '../src/clock';
import type { ClockState, Settings, TimelineSlot } from '../src/protocol';
import { imageChoice, scheduleOpen, toMinutes } from '../src/rules';
import { positionAt } from '../src/timeline';

/** docs/solpersona/test-vectors.json — every port of the clock must reproduce these exactly. */
describe('test-vectors.json', () => {
  for (const c of vectors.positionAt) {
    it(`positionAt: ${c.name}`, () => {
      expect(positionAt(c.timeline as TimelineSlot[], c.clock as ClockState, c.nowMs)).toEqual(c.expect);
    });
  }

  for (const c of vectors.clockOffset) {
    it(`clockOffset: ${c.name}`, () => {
      expect(offsetSample(c.serverTime, c.sentAt, c.receivedAt)).toBe(c.expect);
    });
  }

  it('imageChoice', () => {
    for (const c of vectors.imageChoice.cases) {
      const got = imageChoice(c.quality as Settings['imageQuality'], c.screenWidth, c.canHoldOriginal, c.original, c.display);
      expect(got, JSON.stringify(c)).toBe(c.expect);
    }
  });

  it('schedule', () => {
    for (const c of vectors.schedule.cases) {
      expect(scheduleOpen(c.schedule, toMinutes(c.time)!), JSON.stringify(c)).toBe(c.expect);
    }
  });
});
