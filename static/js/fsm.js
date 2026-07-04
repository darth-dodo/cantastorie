// Finite state machine — ported from habla-hermano (Phase 21).
// Design choices kept: immutable machine definition, invalid transitions are
// warn-and-ignore no-ops (safe for stale callbacks), one onChange callback,
// pure state logic — side effects live in the handler.

export function createMachine(config) {
  return Object.freeze({ initial: config.initial, states: config.states });
}

export function interpret(machine, onChange) {
  let current = machine.initial;
  let stopped = false;
  let callback = onChange ?? null;

  return {
    get state() {
      return current;
    },

    send(event) {
      if (stopped) return;

      const stateConfig = machine.states[current];
      const transitions = stateConfig?.on ?? stateConfig;
      if (!transitions || !(event in transitions)) {
        console.warn(`FSM: no transition for event "${event}" in state "${current}"`);
        return;
      }

      const prev = current;
      current = transitions[event];
      callback?.(current, prev, event);
    },

    matches(state) {
      return current === state;
    },

    stop() {
      stopped = true;
      callback = null;
    },
  };
}
