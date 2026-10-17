(() => {
  const params = new URLSearchParams(location.search);
  // The job workspace design is the default (user decision, 2026-10-17). The old app stays reachable:
  // ?classic=1 opens it, and the engineer tools (?engineer=1) and manual AI task review (?operator=1) run in it.
  const classic = params.has("classic") || params.has("engineer") || params.has("operator");
  if (!classic) {
    document.body.classList.add("job-preview");
    document.write('<script type="module" src="/frontend/js/job/app.js"></script>');
    return;
  }
  const scripts = ["/frontend/js/app.js", "/frontend/js/autonomous_tasks.js", "/frontend/js/workspace.js"];
  // This script is loaded at the end of the HTML document. Use parser-inserted
  // scripts for the legacy engineering bundle so callers that navigate to
  // ?engineer=1 can rely on its globals as soon as `load` completes.
  for (const src of scripts) document.write(`<script src="${src}"><\/script>`);
})();
