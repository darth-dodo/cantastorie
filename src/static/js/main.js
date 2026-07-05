// Boot: theme, store, render loop, and the page timer that stands in for
// narration until the audio engine lands.

import { createStore } from "./store.js";
import { load, save } from "./storage.js";
import {
  buildShelf,
  buildPlayer,
  updatePlayer,
  buildChoiceOverlay,
  buildResumeOverlay,
  buildEnd,
} from "./screens.js";

const PAGE_SECONDS = 3.8;

function pickTheme(params, hour) {
  const forced = params.get("theme");
  if (forced === "light" || forced === "dusk") return forced;
  return hour >= 19 || hour < 7 ? "dusk" : "light";
}

export function init(root = document) {
  const app = root.querySelector("#app");
  if (!app) return null;

  const params = new URLSearchParams(root.defaultView?.location.search ?? "");
  const theme = pickTheme(params, new Date().getHours());
  root.documentElement.dataset.theme = theme;

  const store = createStore(load());
  const greeting = theme === "dusk" ? "Buonasera!" : "Ciao!";

  let shown = { screen: null, choiceOpen: false, resumeOpen: false };
  let playerScreen = null;

  function render(state) {
    save(state);

    const structural =
      state.screen !== shown.screen ||
      state.choiceOpen !== shown.choiceOpen ||
      state.resumeOpen !== shown.resumeOpen;

    if (structural) {
      app.replaceChildren();
      if (state.screen === "shelf") {
        playerScreen = null;
        app.appendChild(buildShelf(store, greeting));
      } else if (state.screen === "player") {
        playerScreen = buildPlayer(store);
        app.appendChild(playerScreen);
        if (state.choiceOpen) playerScreen.appendChild(buildChoiceOverlay(store));
        if (state.resumeOpen) playerScreen.appendChild(buildResumeOverlay(store));
      } else {
        playerScreen = null;
        app.appendChild(buildEnd(store));
      }
      shown = { screen: state.screen, choiceOpen: state.choiceOpen, resumeOpen: state.resumeOpen };
    }

    if (state.screen === "player" && playerScreen) updatePlayer(playerScreen, state);
  }

  store.subscribe(render);
  render(store.state);

  const seconds = Number(params.get("speed")) || PAGE_SECONDS;
  const timer = setInterval(() => store.advance(), seconds * 1000);

  return { store, stop: () => clearInterval(timer) };
}

if (typeof document !== "undefined" && document.querySelector("#app")) {
  init();
}
