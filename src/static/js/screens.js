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
  beadColors: story.beadColors,
  images: null,
};

export function playerView(loaded) {
  return {
    pageCount: loaded.pages.length,
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
  onOpenSettings = () => {},
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
      const card = el("div", "cover-card");
      const cover = el("button", `cover ${entry.wash}`, { "aria-label": name });
      if (entry.cover) {
        const img = el("img", "cover-art");
        img.src = entry.cover;
        img.alt = "";
        img.loading = "lazy";
        cover.appendChild(img);
      }
      cover.addEventListener("click", () => onOpen(entry));
      const caption = el("span", "cover-caption");
      caption.textContent = name;
      card.append(cover, caption);
      covers.appendChild(card);
    });
  }

  const gear = el("button", "settings-gear", { "aria-label": "Settings" });
  gear.innerHTML = GEAR_SVG;
  gear.addEventListener("click", onOpenSettings);

  const parent = el("a", "parent-corner");
  parent.href = "/parent";
  parent.textContent = "parent";

  screen.append(header, covers, gear, parent);
  return screen;
}

const GEAR_SVG =
  '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1"/></svg>';

function buildSelect({ options, current, onChange, menuDir = "down" }) {
  const wrap = el("div", "settings-select", { "data-menu-dir": menuDir });
  const button = el("button", "settings-select-current", {
    "aria-haspopup": "listbox",
    "aria-expanded": "false",
  });
  const label = el("span", "settings-select-label");
  const chevron = el("span", "settings-select-chevron", { "aria-hidden": "true" });
  chevron.textContent = "▾";
  button.append(label, chevron);

  const menu = el("div", "settings-menu", { role: "listbox", "aria-hidden": "true" });
  const items = options.map((opt) => {
    const item = el("button", "settings-menu-item", {
      role: "option",
      "aria-current": String(opt.value === current),
    });
    item.textContent = opt.label;
    item.addEventListener("click", () => {
      onChange(opt.value);
      label.textContent = opt.label;
      items.forEach((it) => it.setAttribute("aria-current", String(it === item)));
      setOpen(false);
    });
    menu.appendChild(item);
    return item;
  });

  label.textContent = options.find((opt) => opt.value === current)?.label ?? "";

  function positionMenu() {
    const rect = button.getBoundingClientRect();
    menu.style.inset = "auto";
    menu.style.width = `${rect.width}px`;
    menu.style.left = `${rect.left}px`;
    menu.style.top =
      wrap.dataset.menuDir === "up"
        ? `${rect.top - menu.offsetHeight - 6}px`
        : `${rect.bottom + 6}px`;
  }

  function setOpen(open) {
    document.querySelectorAll(".settings-menu").forEach((m) => {
      if (m !== menu) m.setAttribute("aria-hidden", "true");
    });
    if (open) {
      menu.style.visibility = "hidden";
      menu.setAttribute("aria-hidden", "false");
      positionMenu();
      menu.style.visibility = "";
    } else {
      menu.setAttribute("aria-hidden", "true");
    }
    button.setAttribute("aria-expanded", String(open));
  }

  button.addEventListener("click", (event) => {
    event.stopPropagation();
    setOpen(menu.getAttribute("aria-hidden") === "true");
  });

  wrap.append(button, menu);
  return wrap;
}

export function buildSettingsOverlay({
  langs = [],
  currentLang = "it",
  onLangChange = () => {},
  palettes = [],
  paletteLabels = {},
  currentPalette = "indigo",
  onPaletteChange = () => {},
  onClose = () => {},
}) {
  const overlay = el("div", "overlay settings");
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) onClose();
  });

  const panel = el("div", "settings-panel");

  const langSection = el("div", "settings-section");
  const langLabel = el("div", "settings-label");
  langLabel.textContent = "Language";
  langSection.append(
    langLabel,
    buildSelect({
      options: langs.map((l) => ({ value: l.code, label: l.label })),
      current: currentLang,
      onChange: onLangChange,
    }),
  );

  const themeSection = el("div", "settings-section");
  const themeLabel = el("div", "settings-label");
  themeLabel.textContent = "Theme";
  themeSection.append(
    themeLabel,
    buildSelect({
      options: palettes.map((p) => ({ value: p, label: paletteLabels[p] ?? p })),
      current: currentPalette,
      onChange: onPaletteChange,
      menuDir: "up",
    }),
  );

  const done = el("button", "settings-done");
  done.textContent = "Done";
  done.addEventListener("click", onClose);

  panel.append(langSection, themeSection, done);
  overlay.appendChild(panel);
  return overlay;
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

  const beads = el("div", "beads");
  view.beadColors.forEach((color, i) => {
    const bead = el("div", "bead", { "data-bead": i });
    bead.style.background = color;
    beads.appendChild(bead);
  });
  screen.appendChild(beads);

  const exit = el("button", "exit", { "aria-label": "back to stories" });
  const grid = el("div", "grid");
  for (let i = 0; i < 4; i++) grid.appendChild(el("div"));
  exit.appendChild(grid);
  exit.addEventListener("click", () => store.exitStory());
  screen.appendChild(exit);

  const playPause = el("button", "play-pause", { "aria-label": "play" });
  playPause.addEventListener("click", () => store.togglePlay());
  screen.appendChild(playPause);

  const prev = el("button", "nav nav-prev", { "aria-label": "previous page" });
  prev.appendChild(el("div", "chevron-left"));
  prev.addEventListener("click", () => store.prevPage());
  screen.appendChild(prev);

  const next = el("button", "nav nav-next", { "aria-label": "next page" });
  next.appendChild(el("div", "chevron-right"));
  next.addEventListener("click", () => store.nextPage());
  screen.appendChild(next);

  return screen;
}

export function updatePlayer(screen, state, view = mockView) {
  screen.querySelectorAll(".page-wash").forEach((wash, i) => {
    wash.classList.toggle("current", i === state.page);
  });
  screen.querySelectorAll(".page-art").forEach((art, i) => {
    art.classList.toggle("current", i === state.page);
  });
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
  playPause.setAttribute("aria-label", state.playing ? "pause" : "play");

  screen.querySelector(".nav-prev").classList.toggle("disabled", state.page === 0);
}

// buildChoiceOverlay(view, store, onChoose): `view` is the loaded story's
// resolved choice ({ prompt, options: [{ label, card_image, wash?, ... }] }).
// A published option carries a card_image URL and renders an <img>; the mock
// shelf and dev fixture have no cards, so the CSS wash face stays the fallback.
// Without a view (a story-less cover), the mock choice backs the overlay.
export function buildChoiceOverlay(view, store, onChoose) {
  const choice = view ?? story.choice;
  const overlay = el("div", "overlay");
  const prompt = el("div", "prompt");
  prompt.textContent = choice.prompt;
  const options = el("div", "options");
  choice.options.forEach(({ label, wash, card_image }, index) => {
    const option = el("button", "option", { "aria-label": label });
    // The wash class stays on the card either way; when a card image exists
    // it layers on top, otherwise the wash face is what shows.
    const card = el("div", wash ? `choice-card ${wash}` : "choice-card");
    if (card_image) {
      // Decorative — the button's aria-label already carries the label.
      const img = el("img", "choice-card-image", { alt: "" });
      img.src = card_image;
      card.appendChild(img);
    } else {
      const caption = el("span");
      caption.textContent = label;
      card.appendChild(caption);
    }
    const pill = el("div", "pill");
    pill.textContent = label;
    option.append(card, pill);
    // The tapped option follows its branch: main.js's onChoose extends the
    // played path, then advances. Without it, the store still turns the page.
    option.addEventListener("click", () => (onChoose ? onChoose(index) : store.choose(index)));
    options.appendChild(option);
  });
  overlay.append(prompt, options);
  return overlay;
}

export function buildResumeOverlay(store, resumeText = "Welcome back! Continue or start over?") {
  const overlay = el("div", "overlay");
  const prompt = el("div", "prompt");
  const title = el("strong");
  title.textContent = "Welcome back!";
  const sub = el("small");
  sub.textContent = resumeText;
  prompt.append(title, sub);

  const options = el("div", "options");
  options.append(
    blobOption({ label: "Continue", icon: iconPlay(), onTap: () => store.resumeContinue() }),
    blobOption({ label: "Start over", icon: iconReplay(), onTap: () => store.resumeRestart() }),
  );
  overlay.append(prompt, options);
  return overlay;
}

// The offline state (AI-367): the shelf manifest failed on cold load.
// The whole screen is the retry button; each tap speaks the line again.
export function buildOffline(onRetry) {
  const screen = el("button", "screen offline", { "aria-label": "try again" });
  const clouds = el("div", "clouds");
  for (let i = 0; i < 3; i++) clouds.appendChild(el("div", "puff"));
  const prompt = el("div", "prompt");
  prompt.textContent = "The clouds took the stories. Try again soon!";
  screen.append(clouds, prompt);
  screen.addEventListener("click", onRetry, { once: true });
  return screen;
}

// The audio-retry state (AI-367): narration failed to load. The whole
// overlay is one big tap target — never a small button for small hands.
export function buildAudioError(store) {
  const overlay = el("button", "overlay audio-error", { "aria-label": "try again" });
  // A hand-painted sleeping bird (illustrate pipeline, locked watercolor
  // style), bundled same-origin so the overlay never depends on the network
  // it is reacting to. Decorative — the overlay's aria-label carries meaning.
  const bird = el("img", "bird", { src: "/static/img/sleeping-bird.webp", alt: "" });
  const prompt = el("div", "prompt");
  prompt.textContent = "Oh! The story is taking a nap. Tap the bird to wake it up.";
  overlay.append(bird, prompt);
  overlay.addEventListener("click", () => store.retryAudio());
  return overlay;
}

export function buildEnd(store, endText = { title: "The End!", again: "Again!", prompt: "Another story?" }) {
  const screen = el("div", "screen end night");

  const stars = el("div", "stars");
  stars.style.top = "120px";
  stars.style.left = "70px";
  screen.appendChild(stars);

  const title = el("h2");
  title.textContent = endText.title;

  const options = el("div", "options");
  options.append(
    blobOption({ label: endText.again, icon: iconReplay(), onTap: () => store.replay() }),
    blobOption({ label: endText.prompt, icon: iconShelf(), onTap: () => store.toShelf() }),
  );

  screen.append(title, options);
  return screen;
}
