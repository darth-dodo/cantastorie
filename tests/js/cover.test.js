import { describe, it, expect } from 'vitest';
import { coverSrc } from '../../src/static/js/story.js';

describe('coverSrc', () => {
  it('prefers cover when present', () => {
    expect(coverSrc({ cover: 'c.png', pages: [{ image: 'p0.png' }] })).toBe('c.png');
  });
  it('falls back to first page image when cover missing', () => {
    expect(coverSrc({ cover: null, pages: [{ image: 'p0.png' }] })).toBe('p0.png');
  });
  it('returns null when neither present', () => {
    expect(coverSrc({})).toBe(null);
  });
});
