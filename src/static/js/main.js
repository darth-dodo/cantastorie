// Player entry point. Marks the shell as loaded; the shelf and story
// playback modules will mount into #app as Phase 1 lands.
export function init(root = document) {
  root.querySelector("#app")?.setAttribute("data-player", "ready");
}

init();
