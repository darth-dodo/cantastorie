import { describe, it, expect, vi } from 'vitest';
import { gateOptions, checkGate } from '../../src/static/js/screens.js';

describe('gate', () => {
  it('offers three options including the correct sum', () => {
    const { options, answer } = gateOptions(7, 6);
    expect(answer).toBe(13);
    expect(options).toContain(13);
    expect(options).toHaveLength(3);
  });
  it('wrong answer does not pass', () => {
    const onPass = vi.fn();
    checkGate(12, 13, onPass); expect(onPass).not.toHaveBeenCalled();
    checkGate(13, 13, onPass); expect(onPass).toHaveBeenCalledOnce();
  });
});
