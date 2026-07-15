// Boot: theme, manifest, store, audio unlock, render loop, and the
// playback loop that lets narration drive the story. The page timer only
// stands in for covers whose stories the pipeline hasn't produced yet.

import { createStore, PAGE_COUNT, CHOICE_PAGE } from "./store.js";
import { load, save } from "./storage.js";
import { createAudioEngine } from "./audio-engine.js";
import { createPlayback } from "./playback.js";
import { createPrefetcher } from "./prefetch.js";
import { loadStory, shelf as fallbackShelf } from "./story.js";
import { VALID_PALETTES } from "./palette-resolve.js";
import {
  buildShelf,
  buildPlayer,
  updatePlayer,
  buildChoiceOverlay,
  buildResumeOverlay,
  buildAudioError,
  buildOffline,
  buildEnd,
  buildSettingsOverlay,
  playerView,
} from "./screens.js";

const PALETTE_LABELS = {
  warm: "Caldo",
  indigo: "Indaco",
  seaglass: "Verdemare",
  plum: "Prugna",
};

const PAGE_SECONDS = 3.8;

const LANGS = [
  { code: "it", label: "Italiano" },
  { code: "es", label: "Español" },
  { code: "en", label: "English" },
];

const GREETINGS = { it: "Ciao!", es: "¡Hola!", en: "Hello!" };
const SUBS = { it: "Quale storia oggi?", es: "¿Qué historia hoy?", en: "Which story today?" };

function pickTheme(params, hour) {
  const forced = params.get("theme");
  if (forced === "light" || forced === "dusk") return forced;
  return hour >= 19 || hour < 7 ? "dusk" : "light";
}

function pickLang(params, saved) {
  const lang = params.get("lang") ?? saved ?? "";
  return /^[a-z]{2}$/.test(lang) ? lang : "it";
}

async function fetchManifest(assetBase, fetchFn, lang) {
  try {
    const res = await fetchFn(`${assetBase}/${lang}/manifest.json`);
    if (!res.ok) throw new Error(`manifest fetch failed (${res.status})`);
    return await res.json();
  } catch (err) {
    // Cold-load failure: the clouds screen (AI-367) holds the boot until
    // a tap retries and the manifest answers.
    console.warn("manifest unavailable", err);
    return null;
  }
}

function loadLang(storage = globalThis.localStorage) {
  try {
    return storage?.getItem("cantastorie-lang") ?? null;
  } catch {
    return null;
  }
}

function saveLang(lang, storage = globalThis.localStorage) {
  try {
    storage?.setItem("cantastorie-lang", lang);
  } catch {
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
  let lang = pickLang(params, loadLang());

  const store = createStore(load());
  engine ??= createAudioEngine();

  let manifest = fetchFn ? await fetchManifest(assetBase, fetchFn, lang) : null;

  // Same-origin on purpose — NOT assetBase. assetBase is the R2 bucket in
  // prod, which is unreachable in the very offline case this screen handles;
  // the offline prompt must come from the origin that served this page.
  const offlinePromptUrl = `/static/content/${lang}/prompts/offline.wav`;

  while (fetchFn && manifest === null) {
    await new Promise((resolve) => {
      app.replaceChildren(
        buildOffline(() => {
          // The tap is also the wake gesture: unlock, speak, retry.
          engine
            .unlock()
            .then(() => engine.playPrompt(offlinePromptUrl))
            .catch((err) => console.warn("offline prompt skipped", err));
          resolve();
        }),
      );
    });
    manifest = await fetchManifest(assetBase, fetchFn, lang);
  }

  let stories = manifest?.stories ?? fallbackShelf;
  let prefetcher = createPrefetcher({ engine, fetchFn });
  let playback = createPlayback({ store, engine, prefetcher, prompts: manifest?.prompts ?? {} });

  let activeStory = null;
  const storyCache = new Map();

  async function openCover(entry) {
    if (entry?.story && fetchFn) {
      try {
        let pending = storyCache.get(entry.story);
        if (!pending) {
          pending = loadStory(entry.story, fetchFn);
          storyCache.set(entry.story, pending);
          pending.catch(() => storyCache.delete(entry.story));
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

  async function switchLanguage(newLang) {
    if (newLang === lang) return;
    lang = newLang;
    saveLang(lang);
    storyCache.clear();
    activeStory = null;
    playback.clearStory();
    manifest = fetchFn ? await fetchManifest(assetBase, fetchFn, lang) : null;
    stories = manifest?.stories ?? fallbackShelf;
    prefetcher = createPrefetcher({ engine, fetchFn });
    playback = createPlayback({ store, engine, prefetcher, prompts: manifest?.prompts ?? {} });
    store.toShelf();
    shown = { screen: null, choiceOpen: false, resumeOpen: false, audioError: false, settingsOpen };
    render(store.state);
  }

  root.addEventListener(
    "pointerdown",
    (event) => {
      engine
        .unlock()
        .then(() => {
          const url = manifest?.prompts?.greeting;
          if (url && !event.target.closest(".cover") && !event.target.closest(".settings-gear")) {
            return engine.playPrompt(url);
          }
          return undefined;
        })
        .catch((err) => console.warn("greeting skipped", err));
    },
    { capture: true, once: true },
  );

  let settingsOpen = false;
  let shown = { screen: null, choiceOpen: false, resumeOpen: false, audioError: false, settingsOpen: false };
  let playerScreen = null;

  function openSettings() {
    settingsOpen = true;
    render(store.state);
  }

  function closeSettings() {
    settingsOpen = false;
    render(store.state);
  }

  function render(state) {
    save(state);

    const structural =
      state.screen !== shown.screen ||
      state.choiceOpen !== shown.choiceOpen ||
      state.resumeOpen !== shown.resumeOpen ||
      state.audioError !== shown.audioError ||
      settingsOpen !== shown.settingsOpen;

    const view = activeStory ? playerView(activeStory) : undefined;

    if (structural) {
      app.replaceChildren();
      if (state.screen === "shelf") {
        playerScreen = null;
        app.appendChild(
          buildShelf(
            store,
            GREETINGS[lang] ?? "Ciao!",
            SUBS[lang] ?? "Quale storia oggi?",
            stories,
            () => openSettings(),
            (entry) => {
              openCover(entry).catch((err) => console.warn("cover tap failed", err));
            },
          ),
        );
        if (settingsOpen) {
          app.appendChild(
            buildSettingsOverlay({
              langs: LANGS,
              currentLang: lang,
              onLangChange: (newLang) => switchLanguage(newLang),
              palettes: VALID_PALETTES,
              paletteLabels: PALETTE_LABELS,
              currentPalette: root.documentElement.getAttribute("data-palette") || "indigo",
              onPaletteChange: (name) => {
                if (globalThis.cantastoriePalette) globalThis.cantastoriePalette.set(name);
                else root.documentElement.setAttribute("data-palette", name);
              },
              onClose: () => closeSettings(),
            }),
          );
        }
      } else if (state.screen === "player") {
        playerScreen = buildPlayer(store, view);
        app.appendChild(playerScreen);
        if (state.choiceOpen) playerScreen.appendChild(buildChoiceOverlay(store));
        if (state.resumeOpen) playerScreen.appendChild(buildResumeOverlay(store));
        if (state.audioError) playerScreen.appendChild(buildAudioError(store));
      } else {
        playerScreen = null;
        app.appendChild(buildEnd(store));
      }
      shown = { screen: state.screen, choiceOpen: state.choiceOpen, resumeOpen: state.resumeOpen, audioError: state.audioError, settingsOpen };
    }

    if (state.screen === "player" && playerScreen) updatePlayer(playerScreen, state, view);
  }

  store.subscribe(render);
  render(store.state);

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
    lang,
    switchLanguage,
    stop: () => clearInterval(timer),
  };
  if (root.defaultView) root.defaultView.__shell = shell;
  return shell;
}

if (typeof document !== "undefined" && document.querySelector("#app")) {
  init();
}
