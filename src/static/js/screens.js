// Screen rendering. Each build* function returns a detached element;
// render() swaps what #app shows based on store state. No framework —
// the whole child UI is four screens and two overlays.

import { story, shelf } from "./story.js";
import { PAGE_COUNT } from "./store.js";

// What the player screen shows for the open story. The mock backs covers
// whose stories the pipeline hasn't produced yet; playerView() derives a
// view from a loaded story.json.
const mockView = {
  pageCount: PAGE_COUNT,
  captions: story.captions,
  beadColors: story.beadColors,
  images: null,
};

export function playerView(loaded) {
  return {
    pageCount: loaded.pages.length,
    captions: loaded.pages.map((page) => page.text),
    beadColors: loaded.pages.map((_, i) => story.beadColors[i % story.beadColors.length]),
    images: loaded.pages.map((page) => page.imageUrl),
  };
}

function el(tag, className, attrs = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}

function blobOption({ label, icon, onTap }) {
  const option = el("button", "option", { "aria-label": label });
  const blob = el("div", "blob-button");
  blob.appendChild(icon);
  const pill = el("div", "pill");
  pill.textContent = label;
  option.append(blob, pill);
  option.addEventListener("click", onTap);
  return option;
}

function iconPlay() {
  return el("div", "icon-play");
}

function iconReplay() {
  return el("div", "icon-replay");
}

function iconShelf() {
  const grid = el("div", "icon-shelf");
  for (let i = 0; i < 4; i++) grid.appendChild(el("div"));
  return grid;
}

export function buildShelf(
  store,
  greeting,
  subText,
  stories = shelf,
  langs = [],
  currentLang = "it",
  onLangChange = () => {},
  onOpen = () => store.openStory(),
) {
  const screen = el("div", "screen shelf");

  const header = el("div", "greeting");
  const mascot = el("div", "mascot");
  mascot.appendChild(el("div", "smile"));
  const text = el("div");
  const hello = el("h1");
  hello.textContent = greeting;
  const sub = el("p");
  sub.textContent = subText;
  text.append(hello, sub);
  header.append(mascot, text);

  const covers = el("div", "covers");

  if (stories.length === 0) {
    const meadow = el("div", "empty-shelf");
    const bird = el("div", "meadow-bird");
    meadow.appendChild(bird);
    const note = el("p", "empty-shelf-text");
    note.textContent = subText;
    meadow.appendChild(note);
    covers.appendChild(meadow);
  } else {
    stories.forEach((entry) => {
      const name = entry.title ?? entry.label;
      const cover = el("button", `cover ${entry.wash}`, { "aria-label": name });
      if (entry.cover) {
        const img = el("img", "cover-art");
        img.src = entry.cover;
        img.alt = "";
        img.loading = "lazy";
        cover.appendChild(img);
      }
      const caption = el("span");
      caption.textContent = name;
      cover.appendChild(caption);
      cover.addEventListener("click", () => onOpen(entry));
      covers.appendChild(cover);
    });
  }

  const language = el("div", "language-sticker");
  const flag = el("div", `flag flag-${currentLang}`);
  language.appendChild(flag);
  const langLabel = el("span", "lang-label");
  const current = langs.find((l) => l.code === currentLang);
  langLabel.textContent = current?.label ?? currentLang;
  language.appendChild(langLabel);

  if (langs.length > 1) {
    language.classList.add("clickable");
    language.setAttribute("role", "button");
    language.setAttribute("tabindex", "0");
    language.setAttribute("aria-label", "Change language");
    language.addEventListener("click", () => {
      const idx = langs.findIndex((l) => l.code === currentLang);
      const next = langs[(idx + 1) % langs.length];
      onLangChange(next.code);
    });
  }

  const parent = el("a", "parent-corner");
  parent.href = "/workshop";
  parent.textContent = "parent";

  screen.append(header, covers, language, parent);
  return screen;
}

export function buildPlayer(store, view = mockView) {
  const screen = el("div", "screen player night");

  for (let i = 0; i < view.pageCount; i++) {
    screen.appendChild(el("div", `page-wash wash-p${i % PAGE_COUNT}`, { "data-page": i }));
  }

  // Full-bleed page art from the published story, layered over the washes;
  // it crossfades with the same gentle opacity ramp.
  if (view.images) {
    view.images.forEach((imageUrl, i) => {
      const art = el("div", "page-art", { "data-page": i });
      if (imageUrl) art.style.backgroundImage = `url("${imageUrl}")`;
      screen.appendChild(art);
    });
  }

  const stars = el("div", "stars");
  stars.style.top = "120px";
  stars.style.left = "70px";
  screen.appendChild(stars);

  const caption = el("div", "caption");
  caption.appendChild(el("span"));
  screen.appendChild(caption);

  const beads = el("div", "beads");
  view.beadColors.forEach((color, i) => {
    const bead = el("div", "bead", { "data-bead": i });
    bead.style.background = color;
    beads.appendChild(bead);
  });
  screen.appendChild(beads);

  const exit = el("button", "exit", { "aria-label": "torna alle storie" });
  const grid = el("div", "grid");
  for (let i = 0; i < 4; i++) grid.appendChild(el("div"));
  exit.appendChild(grid);
  exit.addEventListener("click", () => store.exitStory());
  screen.appendChild(exit);

  const playPause = el("button", "play-pause", { "aria-label": "play" });
  playPause.addEventListener("click", () => store.togglePlay());
  screen.appendChild(playPause);

  return screen;
}

export function updatePlayer(screen, state, view = mockView) {
  screen.querySelectorAll(".page-wash").forEach((wash, i) => {
    wash.classList.toggle("current", i === state.page);
  });
  screen.querySelectorAll(".page-art").forEach((art, i) => {
    art.classList.toggle("current", i === state.page);
  });
  screen.querySelector(".caption span").textContent = view.captions[state.page] ?? "";
  screen.querySelectorAll(".bead").forEach((bead, i) => {
    bead.classList.toggle("current", i === state.page);
    bead.classList.toggle("past", i < state.page);
  });

  const playPause = screen.querySelector(".play-pause");
  playPause.replaceChildren(
    state.playing ? (() => {
      const pause = el("div", "icon-pause");
      pause.append(el("div"), el("div"));
      return pause;
    })() : iconPlay(),
  );
  playPause.setAttribute("aria-label", state.playing ? "pausa" : "play");
}

export function buildChoiceOverlay(store) {
  const overlay = el("div", "overlay");
  const prompt = el("div", "prompt");
  prompt.textContent = story.choice.prompt;
  const options = el("div", "options");
  story.choice.options.forEach(({ label, wash }) => {
    const option = el("button", "option", { "aria-label": label });
    const card = el("div", `choice-card ${wash}`);
    const caption = el("span");
    caption.textContent = label;
    card.appendChild(caption);
    const pill = el("div", "pill");
    pill.textContent = label;
    option.append(card, pill);
    option.addEventListener("click", () => store.choose());
    options.appendChild(option);
  });
  overlay.append(prompt, options);
  return overlay;
}

export function buildResumeOverlay(store) {
  const overlay = el("div", "overlay");
  const prompt = el("div", "prompt");
  const title = el("strong");
  title.textContent = "Rieccoci!";
  const sub = el("small");
  sub.textContent = "Continuiamo o ricominciamo?";
  prompt.append(title, sub);

  const options = el("div", "options");
  options.append(
    blobOption({ label: "Continuiamo", icon: iconPlay(), onTap: () => store.resumeContinue() }),
    blobOption({ label: "Ricominciamo", icon: iconReplay(), onTap: () => store.resumeRestart() }),
  );
  overlay.append(prompt, options);
  return overlay;
}

export function buildEnd(store) {
  const screen = el("div", "screen end night");

  const stars = el("div", "stars");
  stars.style.top = "120px";
  stars.style.left = "70px";
  screen.appendChild(stars);

  const title = el("h2");
  title.textContent = "Fine!";

  const options = el("div", "options");
  options.append(
    blobOption({ label: "Ancora!", icon: iconReplay(), onTap: () => store.replay() }),
    blobOption({ label: "Un'altra storia", icon: iconShelf(), onTap: () => store.toShelf() }),
  );

  screen.append(title, options);
  return screen;
}
