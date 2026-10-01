Archie — website bundle
=======================

  index.html                    markup only
  css/styles.css                all styles
  js/app.js                     all behaviour
  assets/images/toki-wordmark-exact.png  current logo artwork
  package.json                  local scripts and browser test dependency

There is no src/ or public/ folder — this frontend has no build step, so there is
no source-vs-output split. index.html is both the source and what ships.

IMPORTANT — this is not a static site.
--------------------------------------
The interface calls a Python backend and will not function without it:

  POST /api/upload      store the PDF, return page count
  POST /api/analyse     classify pages, render previews
  POST /api/decisions   save the confirmed selection
  GET  /api/projects    list previous analyses
  GET  /api/analysis    reopen a saved analysis

Uploading to a static host renders the interface, but every action fails.

To run the whole thing, use the full repository and:

  cd ..
  python3 -m backend.web_app --port 8000     # or from this folder: npm run dev

For an isolated permission-free local walkthrough, run:

  ./start_web --test

The upload screen then shows “Run full test walkthrough”. It copies the most
recent analysed Butcher Buffet project (or ARCHIE_TEST_FIXTURE_PROJECT_ID) into
output/web_review/test_runs, analyses that PDF again, and runs the local draft
resolvers. It does not call an AI provider, research site, map, or weather API.
Only the selected test-run directory can be reset from the page. This mode is
available only with ARCHIE_ENV=test, ARCHIE_TEST_MODE=1, and a loopback bind.

Requires poppler for page previews and page counts:  brew install poppler

Browser test setup
------------------
Install the JavaScript dependencies and Chromium once:

  cd frontend
  npm install
  npx playwright install chromium

After a UI change, rerun the upload → analyse → confirm journey with:

  npm run test:journey

Run all browser journeys with:

  npm run test:frontend

Run the complete test gate with:

  npm test

The browser smoke tests start the Python backend on a test-only local port and
mock upload/analysis responses. They verify the served HTML and JavaScript work
together without processing a real PDF.

Asset paths are absolute (/frontend/css/...) because backend/web_app.py serves the repo
root and maps index.html to "/". If you host these files at a different root,
change those five paths in index.html.
