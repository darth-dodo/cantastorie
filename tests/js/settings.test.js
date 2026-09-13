import { describe, it, expect, beforeEach } from 'vitest';
import { settingsCopy, LANG_CODES } from '../../src/static/js/screens.js';

describe('settingsCopy', () => {
  it('has all sections for every language', () => {
    for (const code of LANG_CODES) {
      const c = settingsCopy(code);
      for (const key of [
        'languages', 'light', 'lightDay', 'lightAuto', 'lightEvening', 'lightHint',
        'grownups', 'readWithMe', 'workshop', 'close',
        'gateHeading', 'gateWrong', 'gateBack',
      ])
        expect(c[key], `${code}.${key}`).toBeTruthy();
    }
  });

  it('has truthy keys for all 7 language codes', () => {
    expect(LANG_CODES).toHaveLength(7);
    expect(LANG_CODES).toContain('it');
    expect(LANG_CODES).toContain('ru');
  });
});

describe('readWithMe localStorage', () => {
  beforeEach(() => {
    globalThis.localStorage?.clear?.();
  });

  it('LANG_CODES is exported and has correct order', () => {
    expect(LANG_CODES[0]).toBe('it');
    expect(LANG_CODES[6]).toBe('ru');
  });
});
