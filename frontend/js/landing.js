/* Archie heat load — landing page interaction.

   Three behaviours, one rAF loop, no library:
     1. the plate field leans toward the pointer and separates on scroll
     2. sections arrive as they enter view
     3. the tour video follows scroll progress

   Everything degrades to a static, readable page: if motion is reduced or the
   APIs are missing, content is shown immediately and the field simply sits. */
(function () {
  "use strict";

  var reduced = window.matchMedia &&
                window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- 1. the drafting space ---------- */
  var space  = document.getElementById("space");
  var plates = space ? space.querySelectorAll(".plate") : [];

  /* depth per plate: how far each one is pushed back, and therefore how much
     it moves. Front plates travel most, which is what sells the parallax. */
  var depth = [0.22, 0.52, 0.34, 0.78, 0.30, 1.00];

  var pointer = { x: 0, y: 0 };   /* target, -1..1 */
  var eased   = { x: 0, y: 0 };   /* what we actually render */
  var scrollY = 0;

  if (space) requestAnimationFrame(function () { space.classList.add("lit"); });

  if (!reduced && space) {
    addEventListener("pointermove", function (e) {
      pointer.x = (e.clientX / innerWidth  - 0.5) * 2;
      pointer.y = (e.clientY / innerHeight - 0.5) * 2;
    }, { passive: true });

    /* Leaving the window returns the field to rest rather than freezing it
       mid-lean, which would read as a stall. */
    addEventListener("pointerleave", function () { pointer.x = pointer.y = 0; });
  }

  addEventListener("scroll", function () { scrollY = window.scrollY; }, { passive: true });

  var t = 0;
  function frame() {
    t += 0.0045;

    /* critically damped-ish easing: fast enough to feel attached to the
       cursor, slow enough that it never twitches */
    eased.x += (pointer.x - eased.x) * 0.055;
    eased.y += (pointer.y - eased.y) * 0.055;

    for (var i = 0; i < plates.length; i++) {
      var d = depth[i] || 0.4;

      /* pointer lean */
      var px = -eased.x * 46 * d;
      var py = -eased.y * 30 * d;

      /* slow independent drift, so the space is alive when the pointer is not */
      var dx = Math.sin(t + i * 1.7) * 9 * d;
      var dy = Math.cos(t * 0.82 + i * 2.3) * 7 * d;

      /* scroll separation: plates pull apart and recede as the hero leaves */
      var s  = Math.min(scrollY / (innerHeight || 900), 1);
      var sy = s * 190 * d;
      var sz = -s * 340 * d;

      plates[i].style.setProperty("--tx", (px + dx).toFixed(2) + "px");
      plates[i].style.setProperty("--ty", (py + dy + sy).toFixed(2) + "px");
      plates[i].style.setProperty("--tz", sz.toFixed(2) + "px");
    }
    requestAnimationFrame(frame);
  }
  if (plates.length && !reduced) requestAnimationFrame(frame);

  /* ---------- 2. entry reveals ----------
     One observer for the whole page. Elements opt into an entrance with
     data-anim ("left", "clarify", "clip"); everything else rises. Each one
     fires once and is then released, so scrolling back up does not replay. */
  var sel = ".kicker, .h2, .lede, .subhead, .eyebrow, h1.display, .actions," +
            " .steps li, .ticks li, .figlist li, .scrubfig, .pull, .attrib," +
            " .endcard, .foot .wrap, [data-anim], .strip__cell," +
            " .offsetpair figure, .scrubfig__inset";
  var targets = Array.from(document.querySelectorAll(sel)).filter(function (el) { return !el.matches(".scrubfig, .scrubfig__inset, .strip__cell, .offsetpair figure"); });

  for (var j2 = 0; j2 < targets.length; j2++) targets[j2].classList.add("rv");

  if (reduced || !("IntersectionObserver" in window)) {
    for (var k = 0; k < targets.length; k++) targets[k].classList.add("in");
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add("in");
        io.unobserve(e.target);
      });
    }, { rootMargin: "0px 0px -12% 0px", threshold: 0.01 });

    for (var m = 0; m < targets.length; m++) io.observe(targets[m]);

    requestAnimationFrame(function () {
      var hero = document.querySelectorAll(".tour .rv, .hero .rv");
      for (var h2 = 0; h2 < hero.length; h2++) hero[h2].classList.add("in");
    });
  }

  /* ---------- the tour ----------
     One video, scrubbed by scroll. The section is four viewport-heights tall;
     position through it maps 0..1 onto the video's timeline, so the footage
     only advances as far as you have scrolled. Chapters of copy cross-fade
     over it at fixed points, and a bar reports progress.

     Seeking is done inside a rAF tick and only when the target has actually
     moved, because issuing a seek per scroll event will stall the decoder. */
  var tour = document.getElementById("tour");
  var vid  = document.getElementById("tourVideo");

  /* Narrow screens get the 960-wide encode: 4.6 MB against 7.1, and nothing
     on a phone can resolve the difference. Set before the browser commits to
     a source. */
  if (vid && matchMedia("(max-width: 900px)").matches) {
    vid.setAttribute("src", "/frontend/assets/video/tour-960.mp4");
    vid.load();
  }

  if (tour && vid && !reduced) {
    var chapters = tour.querySelectorAll(".tour__copy");
    var dots     = tour.querySelectorAll(".tour__dot");
    var bar      = tour.querySelector(".tour__progress i");
    var count    = chapters.length;
    var active   = -1;
    var queued   = false;
    var duration = 0;
    var wanted   = 0;

    /* Resolve the duration however it becomes available. Reading it only from
       loadedmetadata is a trap: a cached video already has metadata by the
       time this runs, the event never fires again, duration stays 0, and the
       scrub silently does nothing. */
    function readDuration() {
      if (duration) return;
      var d = vid.duration;
      if (d && isFinite(d) && d > 0) { duration = d; paint(); }
    }
    vid.addEventListener("loadedmetadata", readDuration);
    vid.addEventListener("durationchange", readDuration);
    vid.addEventListener("canplay", readDuration);

    /* A video that has never played will not paint a frame, so until it is
       primed the visitor sees the poster and the hero looks like a still.
       Muted playback needs no gesture; play, pause, and hand over to scroll. */
    var primed = false;
    function prime() {
      if (primed) return;
      primed = true;
      var p = vid.play();
      if (p && p.then) {
        p.then(function () {
          vid.pause();
          readDuration();
          seek(wanted || 0.001);          /* force one painted frame */
        }).catch(function () {
          primed = false;                  /* refused: retry on a gesture */
        });
      } else {
        vid.pause();
        readDuration();
      }
    }

    /* Exact seeks keep the camera attached to scroll, including between
       keyframes. Coalesce updates until the decoder finishes the last seek. */
    function seek(t) {
      t = Math.max(0, Math.min(duration ? duration - 0.05 : 0, t));
      if (!vid.seeking) vid.currentTime = t;
    }

    vid.addEventListener("seeked", function () {
      if (Math.abs(vid.currentTime - wanted) > 0.02) seek(wanted);
    });

    function progress() {
      var r = tour.getBoundingClientRect();
      var track = tour.offsetHeight - innerHeight;
      return track > 0 ? Math.min(1, Math.max(0, -r.top / track)) : 0;
    }

    function paint() {
      queued = false;
      var p = progress();

      if (bar) bar.style.width = (p * 100).toFixed(2) + "%";

      if (duration) {
        wanted = p * (duration - 0.05);
        if (Math.abs(vid.currentTime - wanted) > 0.02) seek(wanted);
      }

      var i = Math.min(count - 1, Math.floor(p * count));
      if (i !== active) {
        active = i;
        for (var n = 0; n < count; n++) {
          chapters[n].classList.toggle("is-on", n === i);
          if (dots[n]) dots[n].classList.toggle("is-on", n === i);
        }
      }
    }

    function onScroll() {
      prime();
      if (queued) return;
      queued = true;
      requestAnimationFrame(paint);
    }

    chapters[0].classList.add("is-on");
    addEventListener("scroll", onScroll, { passive: true });
    addEventListener("resize", onScroll);
    addEventListener("pointerdown", prime, { passive: true });
    addEventListener("keydown", prime);

    readDuration();
    if (vid.readyState >= 2) prime();
    vid.addEventListener("loadeddata", prime, { once: true });
    paint();


    for (var d = 0; d < dots.length; d++) {
      (function (idx) {
        dots[idx].addEventListener("click", function () {
          var track = tour.offsetHeight - innerHeight;
          scrollTo({ top: tour.offsetTop + track * ((idx + 0.5) / count),
                     behavior: "smooth" });
        });
      })(d);
    }
  } else if (tour) {
    /* Reduced motion, or no video: every chapter is simply present and the
       footage holds on its poster frame. */
    var all = tour.querySelectorAll(".tour__copy");
    for (var a = 0; a < all.length; a++) all[a].classList.add("is-on");
  }

  /* ---------- nav hairline ---------- */
  var nav = document.getElementById("nav");
  if (nav) {
    var navState = function () { nav.classList.toggle("scrolled", window.scrollY > 20); };
    addEventListener("scroll", navState, { passive: true });
    navState();
  }
})();

(function () {
  var video = document.getElementById('cityVideo');
  var toggle = document.getElementById('cityMotion');
  if (!video || !toggle) return;
  var motion = window.matchMedia('(prefers-reduced-motion: reduce)');
  function sync() { toggle.textContent = video.paused ? 'Play background video' : 'Pause background video'; }
  function play() { video.play().catch(sync); }
  toggle.hidden = false;
  video.addEventListener('play', sync);
  video.addEventListener('pause', sync);
  toggle.addEventListener('click', function () { if (video.paused) play(); else video.pause(); });
  motion.addEventListener('change', function () { if (motion.matches) video.pause(); });
  if (!motion.matches) play();
})();

(function () {
  var tabs = Array.from(document.querySelectorAll('[data-desk]'));
  if (!tabs.length) return;
  var content = {
    drawing: ['START WITH THE SOURCE', 'One drawing set. Connected evidence.', 'Plans, sections and specifications provide the context for reviewed room and envelope inputs.'],
    inputs: ['BUILD THE MODEL', 'Make the assumptions visible.', 'Review room boundaries, constructions, occupancy and design conditions alongside their sources.'],
    review: ['APPLY ENGINEERING JUDGEMENT', 'Review the inputs. Then the result.', 'Resolve outstanding decisions before calculation, then check hourly cooling loads and their review status.']
  };
  function activate(tab) {
    tabs.forEach(function (item) { item.setAttribute('aria-selected', String(item === tab)); item.tabIndex = item === tab ? 0 : -1; });
    var text = content[tab.dataset.desk];
    document.getElementById('desk-label').textContent = text[0];
    document.getElementById('desk-heading').textContent = text[1];
    document.getElementById('desk-description').textContent = text[2];
    document.getElementById('desk-panel').setAttribute('aria-labelledby', tab.id);
  }
  tabs.forEach(function (tab, index) {
    tab.addEventListener('click', function () { activate(tab); });
    tab.addEventListener('keydown', function (event) {
      var next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = tabs.length - 1;
      else return;
      event.preventDefault(); activate(tabs[next]); tabs[next].focus();
    });
  });
})();
