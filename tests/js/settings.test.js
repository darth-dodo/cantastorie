import { describe, it, expect, beforeEach } from 'vitest';
import {
  settingsCopy,
  LANG_CODES,
  getReadWithMe,
  setReadWithMe,
  buildPlayer,
  updatePlayer,
} from '../../src/static/js/screens.js';

// A minimal player view with per-page text, mirroring what playerView()
// produces from a loaded story.json.
const textView = {
  pageCount: 3,
  beadColors: ['#a', '#b', '#c'],
  images: null,
  texts: ['page one text', 'page two text', 'page three text'],
};

const storeStub = {
  togglePlay() {},
  exitStory() {},
  prevPage() {},
  nextPage() {},
};

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

describe('readWithMe persistence', () => {
  beforeEach(() => {
    globalThis.localStorage?.clear?.();
    document.body.innerHTML = '';
  });

  it('defaults to OFF when nothing is stored', () => {
    expect(getReadWithMe()).toBe(false);
    expect(localStorage.getItem('cantastorie-read-with-me')).toBeNull();
  });

  it('setReadWithMe(true) persists "1" to the cantastorie-read-with-me key', () => {
    setReadWithMe(true);
    expect(localStorage.getItem('cantastorie-read-with-me')).toBe('1');
    expect(getReadWithMe()).toBe(true);
  });

  it('setReadWithMe(false) persists "0" and reads back OFF', () => {
    setReadWithMe(true);
    setReadWithMe(false);
    expect(localStorage.getItem('cantastorie-read-with-me')).toBe('0');
    expect(getReadWithMe()).toBe(false);
  });
});

describe('readWithMe toggles on-screen page text', () => {
  beforeEach(() => {
    globalThis.localStorage?.clear?.();
    document.body.innerHTML = '';
  });

  it('hides all page text when Read-with-me is OFF at build time', () => {
    setReadWithMe(false);
    const screen = buildPlayer(storeStub, textView);
    const texts = [...screen.querySelectorAll('.player-page-text')];
    expect(texts).toHaveLength(3);
    expect(texts.every((t) => t.style.display === 'none')).toBe(true);
  });

  it('shows the current page text when Read-with-me is ON', () => {
    setReadWithMe(true);
    const screen = buildPlayer(storeStub, textView);
    // Simulate the render loop landing on page 1.
    updatePlayer(screen, { page: 1, playing: false }, textView);
    const texts = [...screen.querySelectorAll('.player-page-text')];
    const shown = texts.filter((t) => t.style.display !== 'none');
    expect(shown).toHaveLength(1);
    expect(shown[0].dataset.page).toBe('1');
    expect(shown[0].textContent).toBe('page two text');
  });

  it('setReadWithMe(false) live-hides page text already in the DOM', () => {
    setReadWithMe(true);
    const screen = buildPlayer(storeStub, textView);
    document.body.appendChild(screen);
    updatePlayer(screen, { page: 0, playing: false }, textView);
    expect(screen.querySelector('.player-page-text[data-page="0"]').style.display).not.toBe('none');
    // Turning the toggle OFF must hide the visible text without a re-render.
    setReadWithMe(false);
    const texts = [...screen.querySelectorAll('.player-page-text')];
    expect(texts.every((t) => t.style.display === 'none')).toBe(true);
  });

  it('LANG_CODES is exported in the it..ru order', () => {
    expect(LANG_CODES[0]).toBe('it');
    expect(LANG_CODES[6]).toBe('ru');
  });
});
