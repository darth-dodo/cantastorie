/* parent-stories.js: the parent stories page's filter panel toggle and
   two-step delete arming. A static module so script-src needs no
   'unsafe-inline' (AI-505). */

export function wireParentStories(doc = document) {
  doc.querySelectorAll("[data-filter-wrap]").forEach((wrap) => {
    const btn = wrap.querySelector("[data-filter-toggle]");
    const panel = wrap.querySelector("[data-filter-panel]");
    if (!btn || !panel) return;
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      panel.hidden = !panel.hidden;
    });
    doc.addEventListener("click", () => {
      panel.hidden = true;
    });
  });
  doc.querySelectorAll("[data-arm-wrap]").forEach((wrap) => {
    const trigger = wrap.querySelector("[data-arm-trigger]");
    const confirm = wrap.querySelector("[data-arm-confirm]");
    if (!trigger || !confirm) return;
    trigger.addEventListener("click", () => {
      trigger.hidden = true;
      confirm.hidden = false;
    });
  });
}

if (typeof document !== "undefined") {
  wireParentStories();
}
