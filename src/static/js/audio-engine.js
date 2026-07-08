// The audio engine. One module owns a single AudioContext; everything else
// asks it to play things. Web Audio (decoded buffers + gain nodes), never
// <audio> tags — iOS makes media-element volume read-only, which would kill
// the mandated gentle crossfades.
//
// Two channels with a hard rule between them: narration tells the story,
// prompts speak the UI, and they never overlap. A prompt ducks narration
// (exact-position pause) and hands the story back when it finishes.

import { createMachine, interpret } from "./fsm.js";

export const CROSSFADE_SECONDS = 0.9; // slow crossfades only

const narrationMachine = createMachine({
  initial: "idle",
  states: {
    idle: { PLAY: "playing" },
    playing: { PLAY: "playing", PAUSE: "paused", DUCK: "ducked", END: "idle", STOP: "idle" },
    paused: { PLAY: "playing", RESUME: "playing", STOP: "idle" },
    ducked: { UNDUCK: "playing", PAUSE: "paused", STOP: "idle" },
  },
});

export function createAudioEngine({
  createContext = () => new (globalThis.AudioContext ?? globalThis.webkitAudioContext)(),
  fetchFn = (...args) => globalThis.fetch(...args),
  crossfadeSeconds = CROSSFADE_SECONDS,
} = {}) {
  let ctx = null;
  const buffers = new Map();
  const fsm = interpret(narrationMachine);

  // The live narration voice, if any.
  let narration = null; // { source, gain, url, startOffset, startedAt, onEnded }
  // Where the story stands while paused or ducked.
  let held = null; // { url, offset, onEnded }
  let prompt = null; // { source, gain }
  // A narration commanded but still decoding. Every narration command
  // (play, pause, stop) bumps the epoch; a start that returns from its
  // load to a stale epoch was superseded and must stay silent.
  let pendingStart = null; // { url, offset, onEnded }
  let playEpoch = 0;

  function ensureContext() {
    ctx ??= createContext();
    return ctx;
  }

  function makeVoice(buffer, gainValue) {
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const gain = ctx.createGain();
    gain.gain.value = gainValue;
    source.connect(gain);
    gain.connect(ctx.destination);
    return { source, gain };
  }

  function silence(voice, fade) {
    voice.source._manualStop = true;
    if (fade > 0) {
      voice.gain.gain.setValueAtTime(voice.gain.gain.value, ctx.currentTime);
      voice.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + fade);
      voice.source.stop(ctx.currentTime + fade);
    } else {
      voice.source.stop(0);
    }
  }

  function narrationPosition() {
    if (narration && fsm.matches("playing")) {
      return narration.startOffset + (ctx.currentTime - narration.startedAt);
    }
    return held?.offset ?? 0;
  }

  function holdNarration() {
    if (!narration) return;
    held = { url: narration.url, offset: narrationPosition(), onEnded: narration.onEnded };
    silence(narration, 0);
    narration = null;
  }

  async function startNarration(url, offset, onEnded) {
    const epoch = ++playEpoch;
    pendingStart = { url, offset, onEnded };
    const buffer = await engine.load(url);
    if (epoch !== playEpoch) return; // paused or stopped while loading
    pendingStart = null;
    const fading = narration !== null;
    if (fading) silence(narration, crossfadeSeconds);

    const voice = makeVoice(buffer, 0);
    voice.gain.gain.setValueAtTime(0, ctx.currentTime);
    voice.gain.gain.linearRampToValueAtTime(1, ctx.currentTime + crossfadeSeconds);
    voice.source.start(0, offset);

    narration = { ...voice, url, startOffset: offset, startedAt: ctx.currentTime, onEnded };
    voice.source.onended = () => {
      if (voice.source._manualStop) return;
      narration = null;
      held = null;
      fsm.send("END");
      onEnded?.();
    };
    held = null;
    fsm.send("PLAY");
  }

  const engine = {
    get state() {
      return fsm.state;
    },

    get unlocked() {
      return ctx !== null && ctx.state === "running";
    },

    // Call from the first user gesture: browsers allow no sound before it.
    async unlock() {
      ensureContext();
      if (ctx.state === "suspended") await ctx.resume();
    },

    async load(url) {
      ensureContext();
      if (!buffers.has(url)) {
        buffers.set(
          url,
          fetchFn(url)
            .then((res) => {
              if (!res.ok) throw new Error(`audio fetch failed: ${url} (${res.status})`);
              return res.arrayBuffer();
            })
            .then((data) => ctx.decodeAudioData(data))
            .catch((err) => {
              buffers.delete(url); // one flaky request must not silence the page all session
              throw err;
            }),
        );
      }
      return buffers.get(url);
    },

    async playNarration(url, { offset = 0, onEnded } = {}) {
      ensureContext();
      if (prompt) {
        silence(prompt, 0);
        prompt = null;
      }
      await startNarration(url, offset, onEnded);
    },

    // Returns the exact position, ready for IndexedDB.
    pauseNarration() {
      if (fsm.matches("ducked")) {
        // Pausing mid-prompt sticks: the prompt's end must not un-pause it.
        fsm.send("PAUSE");
        return held?.offset ?? 0;
      }
      playEpoch++; // a voice still loading must not start under a paused UI
      if (pendingStart) {
        if (narration) {
          silence(narration, 0);
          narration = null;
        }
        held = pendingStart;
        pendingStart = null;
        if (fsm.matches("idle")) fsm.send("PLAY"); // the loading voice was the story playing
        if (!fsm.matches("paused")) fsm.send("PAUSE");
        return held.offset;
      }
      if (!fsm.matches("playing") || !narration) return held?.offset ?? 0;
      holdNarration();
      fsm.send("PAUSE");
      return held.offset;
    },

    async resumeNarration() {
      if (!held) return;
      const { url, offset, onEnded } = held;
      await startNarration(url, offset, onEnded);
    },

    position: narrationPosition,

    // Prompts and narration never overlap: narration ducks, the prompt
    // speaks, and the story resumes exactly where it left off.
    async playPrompt(url, { onEnded } = {}) {
      ensureContext();
      const buffer = await engine.load(url);

      if (prompt) {
        silence(prompt, 0); // two prompts never overlap either
        prompt = null;
      }

      const wasTelling = fsm.matches("playing") && narration;
      if (wasTelling) {
        holdNarration();
        fsm.send("DUCK");
      }

      const voice = makeVoice(buffer, 1);
      voice.source.start(0);
      prompt = voice;

      voice.source.onended = async () => {
        if (prompt === voice) prompt = null; // never clear a newer prompt's slot
        if (voice.source._manualStop) return; // a silenced prompt keeps its onEnded to itself
        onEnded?.();
        if (fsm.matches("ducked")) {
          fsm.send("UNDUCK");
          await engine.resumeNarration();
        }
      };
    },

    stopAll() {
      playEpoch++;
      pendingStart = null;
      if (narration) silence(narration, 0);
      if (prompt) silence(prompt, 0);
      narration = null;
      prompt = null;
      held = null;
      if (!fsm.matches("idle")) fsm.send("STOP");
    },
  };

  return engine;
}
