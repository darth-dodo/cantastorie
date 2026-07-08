// Boot: theme, manifest, store, audio unlock, render loop, and the
// playback loop that lets narration drive the story. The page timer only
// stands in for covers whose stories the pipeline hasn't produced yet.

import { createStore, PAGE_COUNT, CHOICE_PAGE } from "./store.js";
import { load, save } from "./storage.js";
import { createAudioEngine } from "./audio-engine.js";
import { createPlayback } from "./playback.js";
import { createPrefetcher } from "./prefetch.js";
import { loadStory, shelf as fallbackShelf } from "./story.js";
import {
  buildShelf,
  buildPlayer,
  updatePlayer,
  buildChoiceOverlay,
  buildResumeOverlay,
  buildEnd,
  playerView,
} from "./screens.js";

const PAGE_SECONDS = 3.8;

function pickTheme(params, hour) {
  const forced = params.get("theme");
  if (forced === "light" || forced === "dusk") return forced;
  return hour >= 19 || hour < 7 ? "dusk" : "light";
}

// First-run language is Italian (product.md); ?lang=xx opens another
// published shelf without a parent gate, which is all the demo needs.
function pickLang(params) {
  const lang = params.get("lang") ?? "";
  return /^[a-z]{2}$/.test(lang) ? lang : "it";
}

async function fetchManifest(assetBase, fetchFn, lang) {
  try {
    const res = await fetchFn(`${assetBase}/${lang}/manifest.json`);
    if (!res.ok) throw new Error(`manifest fetch failed (${res.status})`);
    return await res.json();
  } catch (err) {
    // The offline clouds screen is slice 2 (AI-367); until then the
    // built-in covers keep development working without a server.
    console.warn("manifest unavailable, using built-in shelf", err);
    return null;
  }
}

export async function init(
  root = document,
  { fetchFn = globalThis.fetch?.bind(globalThis), engine = null } = {},
) {
  const app = root.querySelector("#app");
  if (!app) return null;

  const params = new URLSearchParams(root.defaultView?.location.search ?? "");
  const theme = pickTheme(params, new Date().getHours());
  root.documentElement.dataset.theme = theme;

  const assetBase =
    root.querySelector('meta[name="asset-base"]')?.getAttribute("content") ?? "content";
  const lang = pickLang(params);
  const manifest = fetchFn ? await fetchManifest(assetBase, fetchFn, lang) : null;
  const stories = manifest?.stories ?? fallbackShelf;

  const store = createStore(load());
  engine ??= createAudioEngine();
  const prefetcher = createPrefetcher({ engine, fetchFn });
  const playback = createPlayback({ store, engine, prefetcher, prompts: manifest?.prompts ?? {} });
  const greeting = theme === "dusk" ? "Buonasera!" : "Ciao!";

  // The open cover's loaded story.json, if it has one; covers without a
  // published story keep the mock captions and the page timer.
  let activeStory = null;
  const storyCache = new Map();

  async function openCover(entry) {
    if (entry?.story && fetchFn) {
      try {
        // Cache the promise, not the result: a double-tap shares one fetch.
        let pending = storyCache.get(entry.story);
        if (!pending) {
          pending = loadStory(entry.story, fetchFn);
          storyCache.set(entry.story, pending);
          pending.catch(() => storyCache.delete(entry.story)); // retry next tap
        }
        const loaded = await pending;
        activeStory = loaded;
        await playback.openStory(loaded);
        return;
      } catch (err) {
        console.warn("story unavailable, using the page timer", err);
      }
    }
    activeStory = null;
    playback.clearStory();
    store.openStory({ pageCount: PAGE_COUNT, choicePage: CHOICE_PAGE });
  }

  // The first tap anywhere wakes the sound; if it isn't already the cover
  // tap, the shelf greets aloud. Browsers allow no audio before a gesture.
  root.addEventListener(
    "pointerdown",
    (event) => {
      engine
        .unlock()
        .then(() => {
          const url = manifest?.prompts?.greeting;
          if (url && !event.target.closest(".cover")) {
            return engine.playPrompt(url);
          }
          return undefined;
        })
        .catch((err) => console.warn("greeting skipped", err));
    },
    { capture: true, once: true },
  );

  let shown = { screen: null, choiceOpen: false, resumeOpen: false };
  let playerScreen = null;

  function render(state) {
    save(state);

    const structural =
      state.screen !== shown.screen ||
      state.choiceOpen !== shown.choiceOpen ||
      state.resumeOpen !== shown.resumeOpen;

    const view = activeStory ? playerView(activeStory) : undefined;

    if (structural) {
      app.replaceChildren();
      if (state.screen === "shelf") {
        playerScreen = null;
        app.appendChild(
          buildShelf(store, greeting, stories, (entry) => {
            openCover(entry).catch((err) => console.warn("cover tap failed", err));
          }),
        );
      } else if (state.screen === "player") {
        playerScreen = buildPlayer(store, view);
        app.appendChild(playerScreen);
        if (state.choiceOpen) playerScreen.appendChild(buildChoiceOverlay(store));
        if (state.resumeOpen) playerScreen.appendChild(buildResumeOverlay(store));
      } else {
        playerScreen = null;
        app.appendChild(buildEnd(store));
      }
      shown = { screen: state.screen, choiceOpen: state.choiceOpen, resumeOpen: state.resumeOpen };
    }

    if (state.screen === "player" && playerScreen) updatePlayer(playerScreen, state, view);
  }

  store.subscribe(render);
  render(store.state);

  // The page timer stands in for narration only while no real story is
  // loaded — a published story turns its own pages on audio end.
  const seconds = Number(params.get("speed")) || PAGE_SECONDS;
  const timer = setInterval(() => {
    if (!playback.hasStory()) store.advance();
  }, seconds * 1000);

  const shell = {
    store,
    engine,
    playback,
    prefetcher,
    manifestLoaded: manifest !== null,
    stop: () => clearInterval(timer),
  };
  if (root.defaultView) root.defaultView.__shell = shell;
  return shell;
}

if (typeof document !== "undefined" && document.querySelector("#app")) {
  init();
}
