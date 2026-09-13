import { describe, it, expect } from 'vitest';
import { sortShelf, cycleLanguage } from '../../src/static/js/screens.js';

describe('sortShelf', () => {
  it('puts family covers first, preserving order within groups', () => {
    const out = sortShelf([
      { id: 'a', isFamily: false }, { id: 'b', isFamily: true },
      { id: 'c', isFamily: false }, { id: 'd', isFamily: true },
    ]);
    expect(out.map(e => e.id)).toEqual(['b', 'd', 'a', 'c']);
  });
});

describe('cycleLanguage', () => {
  it('cycles through all 7 languages and wraps', () => {
    const order = ['it','es','en','el','de','bg','ru'];
    let cur = 'it'; const seen = [cur];
    for (let i = 0; i < 7; i++) { cur = cycleLanguage(cur); seen.push(cur); }
    expect(seen.slice(0,7)).toEqual(order);
    expect(seen[7]).toBe('it'); // wraps
  });
});
