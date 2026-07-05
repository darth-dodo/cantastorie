import { describe, expect, it, vi } from "vitest";
import { createMachine, interpret } from "../../src/static/js/fsm.js";

const machine = createMachine({
  initial: "idle",
  states: {
    idle: { PLAY: "playing" },
    playing: { on: { PAUSE: "paused" } }, // nested `on` form also supported
    paused: { PLAY: "playing" },
  },
});

describe("fsm", () => {
  it("starts at the initial state and follows transitions", () => {
    const service = interpret(machine);
    expect(service.state).toBe("idle");
    service.send("PLAY");
    expect(service.matches("playing")).toBe(true);
    service.send("PAUSE");
    expect(service.state).toBe("paused");
  });

  it("invalid transitions are warn-and-ignore no-ops", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const service = interpret(machine);
    service.send("PAUSE"); // not valid from idle
    expect(service.state).toBe("idle");
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });

  it("onChange receives next, previous, and the event", () => {
    const onChange = vi.fn();
    const service = interpret(machine, onChange);
    service.send("PLAY");
    expect(onChange).toHaveBeenCalledWith("playing", "idle", "PLAY");
  });

  it("a stopped service ignores events and drops its callback", () => {
    const onChange = vi.fn();
    const service = interpret(machine, onChange);
    service.stop();
    service.send("PLAY");
    expect(service.state).toBe("idle");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("machine definitions are frozen", () => {
    expect(Object.isFrozen(machine)).toBe(true);
  });
});
