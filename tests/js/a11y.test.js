import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  buildSettingsOverlay,
  buildGate,
  buildChoiceOverlay,
  buildResumeOverlay,
  pageAnnouncement,
  settingsCopy,
  LANG_CODES,
} from '../../src/static/js/screens.js';

// Modal overlays carry dialog semantics, keep Tab inside, and close on
// Escape only where closing is an ordinary way out (AI-498, M25).

const key = (target, k, opts = {}) => {
  const event = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...opts });
  target.dispatchEvent(event);
  return event;
};

afterEach(() => {
  document.body.innerHTML = '';
});

describe('settings sheet dialog', () => {
  it('is a modal dialog named in the active language', () => {
    const backdrop = buildSettingsOverlay({ currentLang: 'de' });
    const sheet = backdrop.querySelector('.settings-sheet');
    expect(sheet.getAttribute('role')).toBe('dialog');
    expect(sheet.getAttribute('aria-modal')).toBe('true');
    expect(sheet.getAttribute('aria-label')).toBe(settingsCopy('de').settings);
  });

  it('Escape closes it', () => {
    const onClose = vi.fn();
    const backdrop = buildSettingsOverlay({ onClose });
    document.body.appendChild(backdrop);
    key(backdrop.querySelector('.settings-close-pill'), 'Escape');
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('Tab wraps from the last control to the first and back', () => {
    const backdrop = buildSettingsOverlay({});
    document.body.appendChild(backdrop);
    const first = backdrop.querySelector('.settings-lang-tile');
    const last = backdrop.querySelector('.settings-close-pill');
    last.focus();
    expect(key(last, 'Tab').defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);
    expect(key(first, 'Tab', { shiftKey: true }).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(last);
  });

  it('the workshop row opens the gate with focus inside; Back hands focus to the row', () => {
    const backdrop = buildSettingsOverlay({ currentLang: 'it' });
    document.body.appendChild(backdrop);
    const row = backdrop.querySelector('.settings-row--workshop');
    row.focus();
    row.click();
    const modal = document.querySelector('.gate-modal');
    expect(modal.contains(document.activeElement)).toBe(true);
    document.querySelector('.gate-back').click();
    expect(document.querySelector('.gate-backdrop')).toBeNull();
    expect(document.activeElement).toBe(row);
  });
});

describe('grown-up gate dialog', () => {
  it('is a modal dialog labelled by its heading', () => {
    const gate = buildGate({ lang: 'es' });
    const modal = gate.querySelector('.gate-modal');
    expect(modal.getAttribute('role')).toBe('dialog');
    expect(modal.getAttribute('aria-modal')).toBe('true');
    const heading = gate.querySelector('.gate-heading');
    expect(modal.getAttribute('aria-labelledby')).toBe(heading.id);
    expect(heading.textContent).toBe(settingsCopy('es').gateHeading);
  });

  it('Escape is the same as Back: the gate goes, nothing passes', () => {
    const onPass = vi.fn();
    const gate = buildGate({ onPass });
    document.body.appendChild(gate);
    key(gate.querySelector('.gate-choice'), 'Escape');
    expect(gate.isConnected).toBe(false);
    expect(onPass).not.toHaveBeenCalled();
  });

  it('two gates never share a heading id', () => {
    const a = buildGate().querySelector('.gate-heading').id;
    const b = buildGate().querySelector('.gate-heading').id;
    expect(a).not.toBe(b);
  });
});

describe('player overlays', () => {
  const store = { choose: vi.fn(), resumeContinue: vi.fn(), resumeRestart: vi.fn() };

  it('the choice overlay is a modal dialog labelled by its prompt; Escape never skips the choice', () => {
    const onChoose = vi.fn();
    const overlay = buildChoiceOverlay(undefined, store, onChoose);
    document.body.appendChild(overlay);
    expect(overlay.getAttribute('role')).toBe('dialog');
    expect(overlay.getAttribute('aria-modal')).toBe('true');
    expect(document.getElementById(overlay.getAttribute('aria-labelledby'))).toBe(
      overlay.querySelector('.prompt'),
    );
    key(overlay.querySelector('.option'), 'Escape');
    expect(onChoose).not.toHaveBeenCalled();
    expect(store.choose).not.toHaveBeenCalled();
    expect(overlay.isConnected).toBe(true);
  });

  it('the resume overlay is a modal dialog labelled by its title', () => {
    const overlay = buildResumeOverlay(store);
    expect(overlay.getAttribute('role')).toBe('dialog');
    expect(overlay.getAttribute('aria-modal')).toBe('true');
    expect(overlay.querySelector(`#${overlay.getAttribute('aria-labelledby')}`).textContent).toBe(
      'Welcome back!',
    );
  });
});

describe('pageAnnouncement', () => {
  it('reads one-based pages in English', () => {
    expect(pageAnnouncement('en', 2, 8)).toBe('Page 3 of 8');
  });

  it('has its own wording for every language, with both numbers', () => {
    for (const code of LANG_CODES) {
      const text = pageAnnouncement(code, 4, 9);
      expect(text, code).toContain('5');
      expect(text, code).toContain('9');
      if (code !== 'en') expect(text, code).not.toBe('Page 5 of 9');
    }
  });
});
