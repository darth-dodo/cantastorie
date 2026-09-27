// Screen rendering. Each build* function returns a detached element;
// render() swaps what #app shows based on store state. No framework —
// the whole child UI is four screens and two overlays.

import { story, shelf, coverSrc } from "./story.js";
import { PAGE_COUNT } from "./store.js";
import { resolveTheme, loadThemeMode, saveThemeMode } from "./palette-resolve.js";

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
    texts: loaded.pages.map((page) => page.text ?? null),
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

export function sortShelf(entries) {
  const family = entries.filter((e) => e.isFamily === true);
  const shared = entries.filter((e) => e.isFamily !== true);
  return [...family, ...shared];
}

export const LANG_CODES = ['it', 'es', 'en', 'el', 'de', 'bg', 'ru', 'mr'];
const LANG_FLAGS = { it: '🇮🇹', es: '🇪🇸', en: '🇬🇧', el: '🇬🇷', de: '🇩🇪', bg: '🇧🇬', ru: '🇷🇺', mr: '🇮🇳' };
const LANG_LABELS = { it: 'IT', es: 'ES', en: 'EN', el: 'EL', de: 'DE', bg: 'БГ', ru: 'РУ', mr: 'मरा' };
const LANG_NAMES = { it: 'Italiano', es: 'Español', en: 'English', el: 'Ελληνικά', de: 'Deutsch', bg: 'Български', ru: 'Русский', mr: 'मराठी' };

// Read-with-me: persisted to localStorage. When ON the player shows page text.
const READ_WITH_ME_KEY = 'cantastorie-read-with-me';

export function getReadWithMe(storage = globalThis.localStorage) {
  try { return storage?.getItem(READ_WITH_ME_KEY) === '1'; } catch { return false; }
}

export function setReadWithMe(value, storage = globalThis.localStorage) {
  try { storage?.setItem(READ_WITH_ME_KEY, value ? '1' : '0'); } catch {}
  // Reflect immediately onto any live player text elements
  document.querySelectorAll('.player-page-text').forEach((el) => {
    el.style.display = value ? '' : 'none';
  });
}

// Localized copy for the settings sheet.
// Keys also consumed by Task 10 (gate): gateHeading, gateWrong, gateBack.
const SETTINGS_COPY = {
  it: {
    languages:    'Lingua',
    light:        'Luce',
    lightDay:     'Giorno',
    lightAuto:    'Automatica',
    lightEvening: 'Sera',
    lightHint:    'La sera si scurisce da sola',
    grownups:     'Per i grandi',
    readWithMe:   'Leggi con me',
    workshop:     'Il laboratorio',
    close:        'Fatto',
    gateHeading:  'Codice genitore',
    gateWrong:    'Codice errato, riprova',
    gateBack:     'Indietro',
  },
  es: {
    languages:    'Idioma',
    light:        'Luz',
    lightDay:     'Día',
    lightAuto:    'Automática',
    lightEvening: 'Tarde',
    lightHint:    'Por la tarde se oscurece sola',
    grownups:     'Para mayores',
    readWithMe:   'Leer conmigo',
    workshop:     'El taller',
    close:        'Listo',
    gateHeading:  'Código parental',
    gateWrong:    'Código incorrecto, inténtalo de nuevo',
    gateBack:     'Volver',
  },
  en: {
    languages:    'Language',
    light:        'Light',
    lightDay:     'Day',
    lightAuto:    'By itself',
    lightEvening: 'Evening',
    lightHint:    'Goes dark on its own in the evening',
    grownups:     'For grown-ups',
    readWithMe:   'Read with me',
    workshop:     'The workshop',
    close:        'Done',
    gateHeading:  'Parent code',
    gateWrong:    'Wrong code, try again',
    gateBack:     'Back',
  },
  el: {
    languages:    'Γλώσσα',
    light:        'Φως',
    lightDay:     'Μέρα',
    lightAuto:    'Αυτόματο',
    lightEvening: 'Βράδυ',
    lightHint:    'Σκουραίνει μόνο του το βράδυ',
    grownups:     'Για μεγάλους',
    readWithMe:   'Διάβασε μαζί μου',
    workshop:     'Το εργαστήρι',
    close:        'Έτοιμο',
    gateHeading:  'Κωδικός γονέα',
    gateWrong:    'Λάθος κωδικός, ξαναπροσπάθησε',
    gateBack:     'Πίσω',
  },
  de: {
    languages:    'Sprache',
    light:        'Helligkeit',
    lightDay:     'Tag',
    lightAuto:    'Automatisch',
    lightEvening: 'Abend',
    lightHint:    'Wird abends von selbst dunkler',
    grownups:     'Für Erwachsene',
    readWithMe:   'Lies mit mir',
    workshop:     'Die Werkstatt',
    close:        'Fertig',
    gateHeading:  'Elterncode',
    gateWrong:    'Falscher Code, nochmal versuchen',
    gateBack:     'Zurück',
  },
  bg: {
    languages:    'Език',
    light:        'Светлина',
    lightDay:     'Ден',
    lightAuto:    'Автоматично',
    lightEvening: 'Вечер',
    lightHint:    'Вечерта потъмнява само',
    grownups:     'За възрастни',
    readWithMe:   'Чети с мен',
    workshop:     'Работилницата',
    close:        'Готово',
    gateHeading:  'Родителски код',
    gateWrong:    'Грешен код, опитай пак',
    gateBack:     'Назад',
  },
  ru: {
    languages:    'Язык',
    light:        'Свет',
    lightDay:     'День',
    lightAuto:    'Авто',
    lightEvening: 'Вечер',
    lightHint:    'Вечером темнеет само',
    grownups:     'Для взрослых',
    readWithMe:   'Читай со мной',
    workshop:     'Мастерская',
    close:        'Готово',
    gateHeading:  'Родительский код',
    gateWrong:    'Неверный код, попробуй ещё раз',
    gateBack:     'Назад',
  },
  mr: {
    languages:    'भाषा',
    light:        'प्रकाश',
    lightDay:     'दिवस',
    lightAuto:    'आपोआप',
    lightEvening: 'संध्याकाळ',
    lightHint:    'संध्याकाळी आपोआप अंधार होतो',
    grownups:     'मोठ्यांसाठी',
    readWithMe:   'माझ्यासोबत वाचा',
    workshop:     'कार्यशाळा',
    close:        'झाले',
    gateHeading:  'पालक कोड',
    gateWrong:    'चुकीचा कोड, पुन्हा प्रयत्न करा',
    gateBack:     'मागे',
  },
};

export function settingsCopy(lang) {
  return SETTINGS_COPY[lang] ?? SETTINGS_COPY.en;
}

export function cycleLanguage(current) {
  const idx = LANG_CODES.indexOf(current);
  return LANG_CODES[(idx + 1) % LANG_CODES.length];
}

export function buildShelf(
  store,
  greeting,
  subText,
  stories = shelf,
  onOpenSettings = () => {},
  onOpen = () => store.openStory(),
  lang = 'en',
  onCycleLanguage = () => {},
) {
  const screen = el("div", "screen shelf");

  const header = el("div", "greeting");
  const mascot = el("div", "mascot");
  mascot.appendChild(el("div", "smile"));
  mascot.appendChild(el("div", "crater-a"));
  mascot.appendChild(el("div", "crater-b"));
  mascot.appendChild(el("div", "crater-c"));
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
    sortShelf(stories).forEach((entry) => {
      const name = entry.title ?? entry.label;
      const card = el("div", "cover-card");
      const familyClass = entry.isFamily ? " cover--family" : "";
      const cover = el("button", `cover ${entry.wash}${familyClass}`, { "aria-label": name });
      const src = coverSrc(entry);
      if (src) {
        const img = el("img", "cover-art");
        img.src = src;
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

  // Language sticker — CSS shape, one tap cycles language
  const sticker = el("button", "lang-sticker", { "aria-label": "Change language" });
  const stickerLabel = el("span", "lang-sticker-label");
  stickerLabel.textContent = LANG_LABELS[lang] ?? lang.toUpperCase();
  sticker.appendChild(stickerLabel);
  sticker.addEventListener("click", () => onCycleLanguage(cycleLanguage(lang)));

  // Settings button — 2×2 CSS dots, replaces GEAR_SVG
  const gear = el("button", "settings-gear", { "aria-label": "Settings" });
  const dotsGrid = el("div", "settings-dots");
  for (let i = 0; i < 4; i++) dotsGrid.appendChild(el("div", "settings-dot"));
  gear.appendChild(dotsGrid);
  gear.addEventListener("click", onOpenSettings);

  screen.append(header, covers, sticker, gear);
  return screen;
}

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
  currentLang = "it",
  onLangChange = () => {},
  onPaletteChange = () => {},
  onClose = () => {},
  onWorkshop = () => {},
  doc = globalThis.document,
}) {
  const copy = settingsCopy(currentLang);

  const backdrop = el("div", "settings-backdrop");
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) onClose();
  });

  const sheet = el("div", "settings-sheet");

  // Drag handle (decorative)
  const handle = el("div", "settings-handle");
  sheet.appendChild(handle);

  // ── Section 1: Languages ────────────────────────────────────────────
  const langSection = el("div", "settings-section");
  const langHeading = el("div", "settings-section-label");
  langHeading.textContent = copy.languages;
  const langGrid = el("div", "settings-lang-grid");
  LANG_CODES.forEach((code) => {
    const tile = el("button", "settings-lang-tile", { "aria-label": LANG_NAMES[code] });
    if (code === currentLang) tile.classList.add("selected");
    const flag = el("span", "settings-lang-flag");
    flag.textContent = LANG_FLAGS[code];
    const name = el("span", "settings-lang-name");
    name.textContent = LANG_NAMES[code];
    tile.append(flag, name);
    tile.addEventListener("click", () => {
      langGrid.querySelectorAll(".settings-lang-tile").forEach((t) => t.classList.remove("selected"));
      tile.classList.add("selected");
      onLangChange(code);
    });
    langGrid.appendChild(tile);
  });
  langSection.append(langHeading, langGrid);

  // ── Section 2: Light ─────────────────────────────────────────────────
  const lightSection = el("div", "settings-section");
  const lightHeading = el("div", "settings-section-label");
  lightHeading.textContent = copy.light;
  const lightGrid = el("div", "settings-light-grid");

  // The stored Light choice; dusk is the default (AI-459).
  const currentMode = loadThemeMode() ?? "dusk";
  const lightOptions = [
    { key: "lightDay",     mode: "light" },
    { key: "lightAuto",    mode: "auto" },
    { key: "lightEvening", mode: "dusk" },
  ];

  lightOptions.forEach(({ key, mode }) => {
    const tile = el("button", "settings-light-tile");
    if (mode === currentMode) tile.classList.add("selected");

    // CSS shape icon
    const icon = el("div", `settings-light-icon settings-light-icon--${key}`);
    const label = el("span", "settings-light-label");
    label.textContent = copy[key];
    tile.append(icon, label);
    tile.addEventListener("click", () => {
      lightGrid.querySelectorAll(".settings-light-tile").forEach((t) => t.classList.remove("selected"));
      tile.classList.add("selected");
      saveThemeMode(mode);
      if (doc?.documentElement) doc.documentElement.dataset.theme = resolveTheme("", undefined, mode);
    });
    lightGrid.appendChild(tile);
  });

  const lightHint = el("p", "settings-light-hint");
  lightHint.textContent = copy.lightHint;
  lightSection.append(lightHeading, lightGrid, lightHint);

  // ── Section 3: For grown-ups ──────────────────────────────────────────
  const grownupsSection = el("div", "settings-section");
  const grownupsHeading = el("div", "settings-section-label");
  grownupsHeading.textContent = copy.grownups;

  // Read-with-me toggle row
  const rwmRow = el("div", "settings-row");
  const rwmLabel = el("span", "settings-row-label");
  rwmLabel.textContent = copy.readWithMe;
  const rwmToggle = el("button", "settings-toggle", { role: "switch" });
  const rwmOn = getReadWithMe();
  rwmToggle.setAttribute("aria-checked", String(rwmOn));
  if (rwmOn) rwmToggle.classList.add("on");
  const rwmThumb = el("span", "settings-toggle-thumb");
  rwmToggle.appendChild(rwmThumb);
  rwmToggle.addEventListener("click", () => {
    const next = rwmToggle.getAttribute("aria-checked") !== "true";
    rwmToggle.setAttribute("aria-checked", String(next));
    rwmToggle.classList.toggle("on", next);
    setReadWithMe(next);
  });
  rwmRow.append(rwmLabel, rwmToggle);

  // Workshop row with lock glyph
  const workshopRow = el("button", "settings-row settings-row--workshop");
  const workshopLabel = el("span", "settings-row-label");
  workshopLabel.textContent = copy.workshop;
  const lockIcon = el("div", "settings-lock-icon");
  const lockBody = el("div", "settings-lock-body");
  const lockShackle = el("div", "settings-lock-shackle");
  lockIcon.append(lockShackle, lockBody);
  workshopRow.append(workshopLabel, lockIcon);
  workshopRow.addEventListener("click", () => {
    const gate = buildGate({ lang: currentLang, onPass: () => { gate.remove(); onWorkshop(); } });
    (doc?.body ?? globalThis.document.body).appendChild(gate);
  });

  grownupsSection.append(grownupsHeading, rwmRow, workshopRow);

  // ── Section 4: Close ─────────────────────────────────────────────────
  const closePill = el("button", "settings-close-pill");
  closePill.textContent = copy.close;
  closePill.addEventListener("click", onClose);

  sheet.append(langSection, lightSection, grownupsSection, closePill);
  backdrop.appendChild(sheet);
  return backdrop;
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

  // Page text elements — shown only when "Read with me" is ON.
  // Each overlays the matching page; display toggled by setReadWithMe().
  if (view.texts) {
    const readWithMeOn = getReadWithMe();
    view.texts.forEach((text, i) => {
      if (!text) return;
      const textEl = el("div", "player-page-text", { "data-page": i });
      textEl.textContent = text;
      textEl.style.display = readWithMeOn ? "" : "none";
      screen.appendChild(textEl);
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

  // Show only the text element for the current page, and only when Read-with-me is ON.
  const rwmOn = getReadWithMe();
  screen.querySelectorAll(".player-page-text").forEach((textEl) => {
    const pageIndex = Number(textEl.dataset.page);
    textEl.style.display = rwmOn && pageIndex === state.page ? "" : "none";
  });
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

// ── Math gate (AI-444) ───────────────────────────────────────────────────────
// gateOptions(a, b) → { options: number[3], answer: number }
// answer === a+b; options contains the answer and two distractors, no dupes.
export function gateOptions(a, b) {
  const answer = a + b;
  const distractors = new Set();
  const offsets = [1, 2, 3, 4, 5];
  for (const d of offsets) {
    if (distractors.size >= 2) break;
    if (answer - d !== answer) distractors.add(answer - d);
    if (distractors.size >= 2) break;
    if (answer + d !== answer) distractors.add(answer + d);
  }
  const arr = [answer, ...Array.from(distractors).slice(0, 2)];
  // Shuffle with a stable, non-random approach seeded by answer so tests are
  // deterministic, but still different from the sorted order.
  arr.sort((x, y) => ((x * 7 + answer) % 13) - ((y * 7 + answer) % 13));
  return { options: arr, answer };
}

// checkGate(choice, answer, onPass): calls onPass only if choice === answer.
export function checkGate(choice, answer, onPass) {
  if (choice === answer) onPass();
}

// buildGate({ lang, onPass }) → detached modal element.
// The gate is not dismissible on wrong answer; only the Back link exits.
// onPass is called when the correct sum is tapped.
export function buildGate({ lang = 'en', onPass = () => {} } = {}) {
  const copy = settingsCopy(lang);
  const { options, answer } = gateOptions(7, 6);

  const backdrop = el('div', 'gate-backdrop');

  const modal = el('div', 'gate-modal');

  const heading = el('h2', 'gate-heading');
  heading.textContent = copy.gateHeading;

  const equationRow = el('div', 'gate-equation');
  equationRow.textContent = '7 + 6 = ?';

  const wrongMsg = el('p', 'gate-wrong');
  wrongMsg.textContent = copy.gateWrong;
  wrongMsg.setAttribute('aria-live', 'polite');
  wrongMsg.setAttribute('aria-hidden', 'true');

  const choicesRow = el('div', 'gate-choices');
  options.forEach((num) => {
    const btn = el('button', 'gate-choice', { 'aria-label': String(num) });
    btn.textContent = num;
    btn.addEventListener('click', () => {
      checkGate(num, answer, onPass);
      if (num !== answer) {
        wrongMsg.removeAttribute('aria-hidden');
        wrongMsg.classList.add('gate-wrong--visible');
      }
    });
    choicesRow.appendChild(btn);
  });

  const backBtn = el('button', 'gate-back');
  backBtn.textContent = copy.gateBack;
  backBtn.addEventListener('click', () => backdrop.remove());

  modal.append(heading, equationRow, choicesRow, wrongMsg, backBtn);
  backdrop.appendChild(modal);
  return backdrop;
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
