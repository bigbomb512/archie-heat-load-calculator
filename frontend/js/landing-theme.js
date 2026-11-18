/* Apply before styles paint. Storage may be blocked in private browsing. */
(function () {
  'use strict';
  var preference = window.matchMedia('(prefers-color-scheme: dark)');
  var saved;
  try { saved = localStorage.getItem('toki.landing.theme'); } catch (_) {}
  var explicit = saved === 'light' || saved === 'dark';
  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    var button = document.getElementById('themeToggle');
    if (button) {
      var dark = theme === 'dark';
      button.setAttribute('aria-pressed', String(dark));
      button.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
      button.querySelector('span').textContent = dark ? 'Light mode' : 'Dark mode';
    }
  }
  apply(explicit ? saved : (preference.matches ? 'dark' : 'light'));
  document.addEventListener('DOMContentLoaded', function () {
    apply(document.documentElement.dataset.theme);
    document.getElementById('themeToggle').addEventListener('click', function () {
      var theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      explicit = true;
      try { localStorage.setItem('toki.landing.theme', theme); } catch (_) {}
      apply(theme);
    });
  });
  preference.addEventListener('change', function (event) {
    if (!explicit) apply(event.matches ? 'dark' : 'light');
  });
})();
