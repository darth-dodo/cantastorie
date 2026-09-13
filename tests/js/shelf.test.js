import { describe, it, expect } from 'vitest';
import { sortShelf } from '../../src/static/js/screens.js';

describe('sortShelf', () => {
  it('puts family covers first, preserving order within groups', () => {
    const out = sortShelf([
      { id: 'a', isFamily: false }, { id: 'b', isFamily: true },
      { id: 'c', isFamily: false }, { id: 'd', isFamily: true },
    ]);
    expect(out.map(e => e.id)).toEqual(['b', 'd', 'a', 'c']);
  });
});
