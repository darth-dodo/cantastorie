/* landing.js: one-shot scroll reveal for .l-step-reveal, no loop, no polling.
   Guarded against environments where IntersectionObserver is absent. A static
   file so script-src needs no 'unsafe-inline' (AI-505). */
document.addEventListener("DOMContentLoaded", function () {
  if (!("IntersectionObserver" in window)) return;
  var mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (mq && mq.matches) return;
  var items = document.querySelectorAll(".l-step-reveal");
  var io = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add("l-step-reveal--visible");
          io.unobserve(e.target);
        }
      });
    },
    { threshold: 0.12 },
  );
  items.forEach(function (el) {
    io.observe(el);
  });
});
