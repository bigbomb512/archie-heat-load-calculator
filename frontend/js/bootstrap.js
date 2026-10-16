(() => {
  const params = new URLSearchParams(location.search);
  // Keep the established workspace as the normal product. The guided redesign
  // stays available behind an explicit preview flag until it is ready to replace it.
  const guided = params.has("guided");
  const scripts = guided
    ? ["/frontend/js/guided.js"]
    : ["/frontend/js/app.js", "/frontend/js/autonomous_tasks.js", "/frontend/js/workspace.js"];
  document.body.classList.add(scripts.length === 1 ? "guided-mode" : "engineer-mode");
  // This script is loaded at the end of the HTML document. Use parser-inserted
  // scripts for the legacy engineering bundle so callers that navigate to
  // ?engineer=1 can rely on its globals as soon as `load` completes.
  for (const src of scripts) document.write(`<script src="${src}"><\/script>`);
})();
