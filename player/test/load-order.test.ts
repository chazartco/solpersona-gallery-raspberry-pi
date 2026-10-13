import { describe, expect, it } from 'vitest';
import { loadOrder, shouldUpgrade } from '../src/rules';

const O = 'https://cdn/original.jpg';
const D = 'https://cdn/display.webp';
const P = 'https://cdn/poster.webp';
const F = 'https://mirror/original.jpg';

describe('loading over a slow link', () => {
  it('streams the small copies before the original when nothing is on the card', () => {
    expect(loadOrder([O, D, F, P], new Set(), [D, P])).toEqual([D, P, O, F]);
  });

  it('takes what is on the card first, best first', () => {
    expect(loadOrder([O, D, F, P], new Set([O, D]), [D, P])).toEqual([O, D, P, F]);
    expect(loadOrder([O, D, F, P], new Set([D]), [D, P])).toEqual([D, P, O, F]);
  });

  it('a film is its film: nothing is reordered by size', () => {
    expect(loadOrder(['v1', 'v2'], new Set(['v2']), [])).toEqual(['v2', 'v1']);
  });

  it('a piece shown from its small copy upgrades once the original is on the card', () => {
    expect(shouldUpgrade(D, [O, D, P], new Set([D]))).toBe(false);
    expect(shouldUpgrade(D, [O, D, P], new Set([D, O]))).toBe(true);
    expect(shouldUpgrade(O, [O, D, P], new Set([O]))).toBe(false);
  });
});
