import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const doneFixture = JSON.parse(
  readFileSync(
    new URL("./fixtures/job-plan-bb-phase1-done.json", import.meta.url),
  ),
);
const needsAreaFixture = JSON.parse(
  readFileSync(
    new URL("./fixtures/job-plan-bb-bar-without-area.json", import.meta.url),
  ),
);

const project = {
  id: "job-1",
  name: "Corner cafe",
  site: "1 Main Street",
  pages: 2,
  analysed: true,
  phase1_complete: true,
  phase2_complete: false,
  results_ready: false,
};
const plan = {
  project_id: "job-1",
  status: {
    drawings_confirmed: true,
    phase1_complete: false,
    phase2_complete: false,
    results_ready: false,
    address: "1 Main Street",
    weather_ai_pick: "Sydney",
    weather_ai_why: "Melrose Park site text",
    above: "floor",
    phase1_base_room_load_report_stale: false,
    needs_you: { phase1: 0 },
    subtabs: {
      rooms: {
        measurements: {
          state: "working",
          detail: "Finding the area of 1 room.",
        },
      },
    },
    room_heights: {
      shop: { ceiling_height_mm: 2700, origin: "preliminary_fallback" },
    },
    envelope: {
      shop: {
        traces: [{ trace_id: "trace-shop" }],
        edges: [
          { trace_id: "trace-shop", page: 1, index: 0, boundary: "external" },
          { trace_id: "trace-shop", page: 1, index: 1, boundary: "internal" },
        ],
        roof: "unknown",
      },
    },
  },
  pages: [
    {
      page: 1,
      title: "Ground floor plan",
      image_width_px: 200,
      image_height_px: 120,
      preview_url: "",
    },
  ],
  rooms: [
    {
      room_id: "shop",
      name: "Shop",
      level: "Ground",
      area_m2: 50,
      area_source: "printed_area",
      phase1_kw: null,
      w_m2: null,
      kind: "comfort",
      included: true,
      has_outline: true,
      state: "finding_surfaces",
      source_pages: [1],
    },
  ],
  outlines: [
    {
      room_id: "shop",
      name: "Shop",
      level: "Ground",
      page: 1,
      points: [
        [10, 10],
        [190, 10],
        [190, 110],
        [10, 110],
        [10, 10],
      ],
      label_point: [100, 60],
      line_style: "solid",
      kind: "comfort",
      state: "finding_surfaces",
      source: "reviewer",
    },
  ],
  rooms_without_outline: [],
  rooms_named_not_traced: [],
  phase1_report: {
    room_peaks: [{ room_id: "shop", name: "Shop", design_total_kw: 2.5 }],
    phase1_surface_audit: [
      {
        room_id: "shop",
        room_name: "Shop",
        surface_type: "opaque_wall",
        area_m2: 20,
        u_value_w_m2k: 0.6,
        boundary_temperature_c: 35,
        boundary_source: "Outside air",
        boundary_condition: "external",
        label: "Shop wall 1 to outside",
        total_kw: 0.8,
      },
    ],
    phase1_assumptions: [
      {
        room_id: "shop",
        room_name: "Shop",
        component: "Floor slab",
        reason: "Assumed ground slab from the drawing.",
      },
    ],
  },
};

async function preview(page, overrides = {}) {
  const currentPlan = structuredClone(plan);
  currentPlan.status = { ...currentPlan.status, ...(overrides.status || {}) };
  if (overrides.final_report) currentPlan.final_report = overrides.final_report;
  if (overrides.rooms) currentPlan.rooms = overrides.rooms;
  const currentProject = {
    ...project,
    phase1_complete:
      overrides.status?.phase1_complete ?? project.phase1_complete,
    phase2_complete:
      overrides.status?.phase2_complete ?? project.phase2_complete,
    results_ready: overrides.status?.results_ready ?? project.results_ready,
  };
  await page.route("**/api/projects", (route) =>
    route.fulfill({ json: [currentProject] }),
  );
  await page.route("**/api/new-job**", route => route.fulfill({ json: { state: "done", drawings_ready: true, pages: 38 } }));
  await page.route("**/api/job-plan**", (route) =>
    route.fulfill({ json: currentPlan }),
  );
  await page.route("**/api/page-reading**", (route) =>
    route.fulfill({
      json: { status: "working", enabled: true, read: 1, page_count: 2 },
    }),
  );
  await page.route("**/api/page-extraction**", (route) =>
    route.fulfill({ json: { status: "done", findings: [] } }),
  );
  await page.route("**/api/drawing-tasks**", (route) =>
    route.fulfill({ json: { status: "running", tasks_done: 1 } }),
  );
  await page.route("**/api/skill-workflow**", (route) =>
    route.fulfill({ json: { status: "running" } }),
  );
  await page.route("**/api/job-calculation**", (route) =>
    route.fulfill({ json: { status: "none" } }),
  );
  await page.route("**/api/job-status**", (route) =>
    route.fulfill({
      json: {
        ...plan.status,
        id: project.id,
        name: project.name,
        stage: "drawings",
      },
    }),
  );
  await page.route("**/api/ai-preliminary-model**", (route) =>
    route.fulfill({
      json: {
        phase2_room_inputs: [
          {
            room_id: "shop",
            name: "Shop",
            people: {
              count: {
                value: 4,
                unit: "people",
                origin: "controlled_preliminary_profile",
                rationale: "Typical room use",
              },
            },
            lights: {
              load: {
                value: 400,
                unit: "W",
                origin: "unresolved",
                rationale: "No fixture schedule",
              },
            },
            equipment: [],
          },
        ],
        hourly_ai_preliminary_load_report: {
          included_scope_peak: { design_total_kw: 4.2, safety_factor: 1.1 },
          room_peaks: [
            {
              room_id: "shop",
              name: "Shop",
              area_m2: 50,
              design_total_kw: 4.2,
              components: { people: 0.6, lighting: 0.4, envelope: 3.2 },
            },
          ],
        },
      },
    }),
  );
  await page.route("**/api/result-status**", (route) =>
    route.fulfill({
      json: {
        report_url: "/report.html",
        assumptions: ["Window shading is not resolved"],
      },
    }),
  );
}

function copy(value) {
  return structuredClone(value);
}

async function realContractPreview(page, source = doneFixture) {
  const plan = copy(source);
  const projectId = plan.status.id;
  plan.project_id = projectId;
  await page.route("**/api/projects", (route) =>
    route.fulfill({
      json: [
        {
          id: projectId,
          name: "Butcher Buffet original filename.pdf",
          display_name: "Butcher Buffet at Melrose Park",
          site: "Melrose Central shopping centre",
          address: plan.status.address,
          found_site: plan.status.found_site,
          created_at: "2026-10-12T09:00:00+11:00",
          updated_at: "2026-10-16T12:00:00+11:00",
          stage: "drawings",
          phase1_complete: plan.status.phase1_complete,
          phase2_complete: plan.status.phase2_complete,
          results_ready: plan.status.results_ready,
          needs_you: plan.status.needs_you,
          drawings_confirmed: plan.status.drawings_confirmed,
        },
      ],
    }),
  );
  await page.route("**/api/new-job**", route => route.fulfill({ json: { state: "done", drawings_ready: true, pages: 38 } }));
  await page.route("**/api/job-plan**", (route) =>
    route.fulfill({ json: plan }),
  );
  await page.route("**/api/page-reading**", (route) =>
    route.fulfill({ json: { status: "done", pages_read: 29, page_count: 29 } }),
  );
  await page.route("**/api/page-extraction**", (route) =>
    route.fulfill({ json: { status: "done", findings: [] } }),
  );
  await page.route("**/api/drawing-tasks**", (route) =>
    route.fulfill({ json: { status: "done", tasks_done: 1 } }),
  );
  await page.route("**/api/skill-workflow**", (route) =>
    route.fulfill({ json: { status: "done", findings: [], answers: {} } }),
  );
  await page.route("**/api/job-calculation**", (route) =>
    route.fulfill({ json: { status: "done", phase: "phase1_base_room" } }),
  );
  await page.route("**/api/result-status**", (route) =>
    route.fulfill({ json: { report_url: "/report.html", assumptions: [] } }),
  );
  await page.route("**/api/ai-preliminary-model**", (route) =>
    route.fulfill({
      json: { room_scope: { uses: {} }, phase2_room_inputs: [] },
    }),
  );
}

test("the job workspace is the only interface; old addresses open it too", async ({ page }) => {
  await page.route("**/api/projects", (route) => route.fulfill({ json: [] }));
  for (const address of ["/", "/?classic=1", "/?engineer=1", "/?operator=1"]) {
    await page.goto(address);
    await expect(page.getByRole("heading", { name: "Jobs", exact: true })).toBeVisible();
  }
  await expect(page.locator(".shell")).toHaveCount(0);
});

test("preview renders the Drawings frame and current room outline", async ({
  page,
}) => {
  await preview(page, { status: { stage: "rooms" } });
  await page.goto("/?preview=1#/job/job-1/drawings");
  await expect(
    page.getByRole("heading", { name: "What the AI is doing" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Found so far" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /Rooms/ })).toHaveAttribute(
    "href",
    /#\/job\/job-1\/rooms/,
  );
  await expect(
    page.locator(".job-plan polygon[data-room-id=shop]"),
  ).toBeVisible();
  await expect(page.getByText("Sydney", { exact: false })).toBeVisible();
  await expect(page.locator(".shell")).toBeHidden();
});

// Drawing tasks running after every page was read: the progress the server saves before and after each AI call.
function runningDrawingTasks(overrides = {}) {
  return {
    status: "running", step: "answer", step_label: "The AI is answering 12 drawing tasks (round 2)",
    answered: 31, refused: 2, rounds: 2, calls: 33,
    current_task: "P0_room_outlines", current_task_label: "Tracing the room outlines", current_target: "page-33",
    task_number: 3, tasks_in_round: 12, tasks_done: 2, tasks_remaining: 10,
    request_seconds: 130, seconds_since_progress: 130, request_limit_s: 300, last_problem: "",
    ...overrides,
  };
}

async function liveDrawings(page, sequence) {
  await preview(page, { status: { stage: "drawings" } });
  const counts = { plan: 0, tasks: 0 };
  await page.route("**/api/job-plan**", async (route) => {
    counts.plan += 1;
    await route.fallback();
  });
  await page.route("**/api/page-reading**", (route) =>
    route.fulfill({ json: { status: "done", pages_read: 73, page_count: 73, read: 73 } }),
  );
  await page.route("**/api/drawing-tasks**", (route) => {
    const reply = sequence[Math.min(counts.tasks, sequence.length - 1)];
    counts.tasks += 1;
    return route.fulfill({ json: reply });
  });
  await page.clock.install();
  await page.goto("/?preview=1#/job/job-1/drawings");
  return counts;
}

test("once every page is read, the working stage is the drawing tasks with the current task and wait", async ({
  page,
}) => {
  await liveDrawings(page, [runningDrawingTasks()]);
  const reading = page.locator('[data-stage="pages"]');
  await expect(reading).toHaveClass(/done/);
  await expect(reading).toContainText("73 of 73 pages read.");
  const tracing = page.locator('[data-stage="rooms"]');
  await expect(tracing).toHaveClass(/active/);
  await expect(tracing).toContainText(
    "Tracing the room outlines on page 33: task 3 of 12 in round 2. Waiting for the AI for 2 min 10 s (it stops waiting at 5 min). 31 tasks answered so far.",
  );
  await expect(page.locator(".job-status")).toContainText("Tracing the room outlines on page 33");
});

test("the stage list refreshes in place while drawing tasks run, without re-reading the plan", async ({
  page,
}) => {
  const counts = await liveDrawings(page, [
    runningDrawingTasks(),
    runningDrawingTasks({ task_number: 4, tasks_done: 3, answered: 32, request_seconds: 5,
                          current_task_label: "Finding north", current_task: "P2_north", current_target: "page-1" }),
  ]);
  const tracing = page.locator('[data-stage="rooms"]');
  await expect(tracing).toContainText("task 3 of 12");
  await page.clock.runFor(5000);
  await expect(tracing).toContainText("Finding north on page 1: task 4 of 12 in round 2. Waiting for the AI for 5 s. 32 tasks answered so far.");
  expect(counts.plan).toBe(1);
});

test("a timed-out drawing call is explained, and finishing redraws the screen once", async ({ page }) => {
  const problem = "Tracing the room outlines (page 33) got no reply within 300 seconds; it will be tried once more.";
  const counts = await liveDrawings(page, [
    runningDrawingTasks({ last_problem: problem }),
    { status: "done", step: "skills", answered: 41, refused: 8, rounds: 5, last_problem: problem },
  ]);
  await expect(page.locator("[data-stage-problem]")).toHaveText(problem);
  await page.clock.runFor(5000);
  await expect(page.locator('[data-stage="rooms"]')).toHaveClass(/done/);
  await expect.poll(() => counts.plan).toBe(2);
  await page.clock.runFor(20000);
  // First load, one refresh, then the redraw's own read; nothing is working any more, so polling stopped.
  expect(counts.tasks).toBe(3);
});

test("polling stops when the person leaves the Drawings screen", async ({ page }) => {
  const counts = await liveDrawings(page, [runningDrawingTasks()]);
  await expect(page.locator('[data-stage="rooms"]')).toHaveClass(/active/);
  await page.clock.runFor(5000);
  await expect.poll(() => counts.tasks).toBe(2);
  await page.evaluate(() => { location.hash = "#/job/job-1/rooms"; });
  await expect(page.getByRole("heading", { name: "What the AI is doing" })).toHaveCount(0);
  const before = counts.tasks;
  await page.clock.runFor(20000);
  expect(counts.tasks).toBe(before);
});

test("drawing tasks left unanswered by a failed connection are listed, not ticked, and can be sent again", async ({
  page,
}) => {
  await preview(page, { status: { stage: "drawings" } });
  const posts = [];
  await page.route("**/api/page-reading**", (route) =>
    route.fulfill({ json: { status: "done", pages_read: 38, page_count: 38 } }),
  );
  await page.route("**/api/skill-workflow**", (route) => route.fulfill({ json: { status: "needs_review" } }));
  await page.route("**/api/drawing-tasks**", async (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { status: "running" } });
    }
    return route.fulfill({
      json: {
        status: "done", recovery: { action: "resume", label: "Try again" }, answered: 0, refused: 0, rounds: 1, calls: 3, connection_stopped: true, not_tried: 9,
        unresolved: [
          { task: "P0_dimensions", target: "page-20-dimension-1", label: "Reading the scale from a printed dimension", kind: "connection" },
          { task: "P2_north", target: "page-25", label: "Finding north", kind: "connection" },
        ],
      },
    });
  });
  await page.goto("/?preview=1#/job/job-1/drawings");
  const tracing = page.locator('[data-stage="rooms"]');
  await expect(tracing).not.toHaveClass(/done/);
  await expect(tracing).toContainText(
    "2 drawing tasks got no answer because the connection to the AI service kept failing, so the calls were stopped: Reading the scale from a printed dimension on page 20; Finding north on page 25. 9 more were not tried. Nothing was concluded about the drawings.",
  );
  await expect(page.locator(".job-status")).toContainText(
    "Some drawing tasks got no answer because the connection to the AI service failed.",
  );
  await tracing.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ project_id: "job-1", action: "resume" });
});

test("a stale walls-and-roof review says why and offers Try again instead of looking like it is still working", async ({
  page,
}) => {
  await preview(page, { status: { stage: "phase1", phase1_complete: true } });
  await page.route("**/api/page-reading**", (route) =>
    route.fulfill({ json: { status: "done", pages_read: 38, page_count: 38 } }),
  );
  await page.route("**/api/drawing-tasks**", (route) => route.fulfill({ json: { status: "done", answered: 13 } }));
  await page.route("**/api/skill-workflow**", (route) => route.fulfill({ json: { status: "stale", recovery: { action: "resume", label: "Try again" } } }));
  await page.goto("/?preview=1#/job/job-1/drawings");
  const envelope = page.locator('[data-stage="envelope"]');
  await expect(envelope).not.toHaveClass(/active/);
  await expect(envelope).toContainText("Finished, but the drawings or inputs changed afterwards.");
  await expect(envelope).not.toContainText("Working through");
  await expect(envelope.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("a failed envelope step is named with its reason, and Try again resumes it", async ({ page }) => {
  await preview(page, { status: { stage: "drawings" } });
  const posts = [];
  await page.route("**/api/page-reading**", (route) =>
    route.fulfill({ json: { status: "done", pages_read: 38, page_count: 38 } }),
  );
  await page.route("**/api/drawing-tasks**", (route) => route.fulfill({ json: { status: "done", answered: 12 } }));
  await page.route("**/api/skill-workflow**", async (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { status: "running" } });
    }
    return route.fulfill({
      json: {
        status: "failed", recovery: { action: "resume", label: "Try again" },
        failed_steps: [{
          id: "ceiling_height_volume", label: "Ceiling heights", kind: "answer_rejected",
          reason: "the AI's answer didn't pass the checks (Each inference must be an object with a field name.)",
          uses_it: ["Wall, roof and floor areas"],
        }],
      },
    });
  });
  await page.goto("/?preview=1#/job/job-1/drawings");
  const envelope = page.locator('[data-stage="envelope"]');
  await expect(envelope).toContainText(
    "Ceiling heights: the AI's answer didn't pass the checks (Each inference must be an object with a field name.). The other steps finished and are kept. Try again redoes ceiling heights, and wall, roof and floor areas if its answer changes.",
  );
  await expect(page.locator(".job-status")).toContainText("Ceiling heights stopped. Try again to continue; finished steps are kept.");
  await expect(page.locator('[data-stage="rooms"]')).toHaveClass(/done/);   // the drawing tasks are not blamed
  await envelope.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ project_id: "job-1", action: "resume", scope: "phase1_envelope" });
});

// The drawing review has finished; Phase 1 is started by the server, never by this page.
async function reviewFinished(page, calculations) {
  await preview(page, { status: { stage: "phase1", phase1_complete: false } });
  const seen = { gets: 0, posts: [] };
  await page.route("**/api/page-reading**", (route) =>
    route.fulfill({ json: { status: "done", pages_read: 38, page_count: 38 } }),
  );
  await page.route("**/api/drawing-tasks**", (route) => route.fulfill({ json: { status: "done", answered: 12 } }));
  await page.route("**/api/skill-workflow**", (route) => route.fulfill({ json: { status: "needs_review" } }));
  await page.route("**/api/job-calculation**", (route) => {
    if (route.request().method() === "POST") {
      seen.posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { status: "queued", phase: "phase1_base_room" } });
    }
    const reply = calculations[Math.min(seen.gets, calculations.length - 1)];
    seen.gets += 1;
    return route.fulfill({ json: reply });
  });
  await page.clock.install();
  await page.goto("/?preview=1#/job/job-1/drawings");
  return seen;
}

test("after the review finishes the page waits for the server's Phase 1 start and never starts it itself", async ({
  page,
}) => {
  const seen = await reviewFinished(page, [
    { status: "none", readiness: { ready: true } },
    { status: "running", phase: "phase1_base_room", step_label: "Calculating the room load" },
  ]);
  const calculation = page.locator('[data-stage="calculation"]');
  await expect(calculation).toHaveClass(/active/);
  await expect(calculation).toContainText("Starting the empty-room load calculation.");
  await page.clock.runFor(5000);
  await expect(calculation).toContainText("Calculating the room load");
  expect(seen.posts).toEqual([]);
});

test("Calculate now requires server readiness and an automatic startup failure", async ({ page }) => {
  const seen = await reviewFinished(page, [{ status: "none", readiness: { ready: true }, offer_start: true }]);
  const calculation = page.locator('[data-stage="calculation"]');
  await expect(calculation.getByRole("button", { name: "Calculate now" })).toBeVisible();
  expect(seen.posts).toEqual([]);
  await calculation.getByRole("button", { name: "Calculate now" }).click();
  await expect.poll(() => seen.posts.length).toBe(1);
  expect(seen.posts[0]).toEqual({ project_id: "job-1", phase: "phase1_base_room", reviewer: "Job workspace" });
});

test("a blocked review never unlocks calculation after a minute while pages are reading", async ({ page }) => {
  await preview(page, { status: { phase1_complete: false } });
  await page.route("**/api/page-reading**", route => route.fulfill({ json: { status: "running", read: 4, page_count: 38,
    recovery: { action: "restart", label: "Restart this step" } } }));
  await page.route("**/api/skill-workflow**", route => route.fulfill({ json: { status: "blocked",
    recovery: { action: "resume", label: "Check and resume", reason: "Waiting for reading the pages to finish." } } }));
  await page.route("**/api/job-calculation**", route => route.fulfill({ json: { status: "none", offer_start: false,
    readiness: { ready: false, reasons: ["Waiting for reading the pages to finish."] } } }));
  await page.route("**/api/page-extraction**", route => route.fulfill({ json: { status: "none",
    recovery: { action: "resume", label: "Check and resume", reason: "Waiting for reading the pages to finish." } } }));
  await page.route("**/api/drawing-tasks**", route => route.fulfill({ json: { status: "none",
    recovery: { action: "resume", label: "Check and resume", reason: "Waiting for reading the pages to finish." } } }));
  await page.clock.install();
  await page.goto("/?preview=1#/job/job-1/drawings");
  await expect(page.locator('[data-stage="reading"], [data-stage="pages"]').getByRole("button", { name: "Restart this step" })).toBeVisible();
  await expect(page.locator('[data-stage="envelope"]').getByRole("button", { name: "Check and resume" })).toBeVisible();
  for (let tick = 0; tick < 15; tick++) await page.clock.runFor(5000);
  await expect(page.getByRole("button", { name: "Calculate now" })).toHaveCount(0);
  await expect(page.locator(".job-status")).toContainText("4 of 38 pages read");
  await page.screenshot({ path: "test-results/recovery-pages-reading.png", fullPage: true });
});

test("restart stays disabled until the old worker exits and resumes with one request", async ({ page }) => {
  await preview(page, { status: { phase1_complete: false } });
  let state = "running";
  let runId = "read-1";
  const posts = [];
  await page.route("**/api/page-reading**", async route => {
    if (route.request().method() === "POST") { posts.push(route.request().postDataJSON()); state = "stopping"; }
    return route.fulfill({ json: { status: state, job_id: runId, read: 4, page_count: 38,
      recovery: state === "stopping" ? { action: null, label: "Restarting…", reason: "Waiting for the current AI request to finish." }
        : { action: "restart", label: "Restart this step" } } });
  });
  await page.route("**/api/page-extraction**", route => route.fulfill({ json: { status: "none",
    recovery: { action: "resume", label: "Check and resume", reason: "Waiting for reading the pages to finish." } } }));
  await page.route("**/api/drawing-tasks**", route => route.fulfill({ json: { status: "none",
    recovery: { action: "resume", label: "Check and resume", reason: "Waiting for reading the pages to finish." } } }));
  await page.route("**/api/skill-workflow**", route => route.fulfill({ json: { status: "not_started",
    recovery: { action: "resume", label: "Check and resume", reason: "Waiting for reading the pages to finish." } } }));
  await page.route("**/api/job-calculation**", route => route.fulfill({ json: { status: "none",
    readiness: { ready: false, reasons: ["Waiting for reading the pages to finish."] } } }));
  await page.clock.install();
  await page.goto("/?preview=1#/job/job-1/drawings");
  const reading = page.locator('[data-stage="pages"]');
  await reading.getByRole("button", { name: "Restart this step" }).click();
  await expect(reading.getByRole("button", { name: "Restarting…", exact: true })).toBeDisabled();
  await page.clock.runFor(10000);
  expect(posts).toEqual([{ project_id: "job-1", action: "restart", expected_run_id: "read-1" }]);
  await page.reload();
  await expect(reading.getByRole("button", { name: "Restarting…", exact: true })).toBeDisabled();
  await page.screenshot({ path: "test-results/recovery-restarting.png", fullPage: true });
  state = "running";
  runId = "read-2";
  await page.clock.runFor(5000);
  await expect(reading.getByRole("button", { name: "Restart this step" })).toBeEnabled();
});

test("a failed status refresh keeps confirmed controls and retries polling", async ({ page }) => {
  await preview(page, { status: { phase1_complete: false } });
  let fail = false;
  await page.route("**/api/page-reading**", route => fail ? route.fulfill({ status: 503, json: { error: "Unavailable" } })
    : route.fulfill({ json: { status: "running", read: 3, page_count: 38,
      recovery: { action: "restart", label: "Restart this step" } } }));
  await page.clock.install();
  await page.goto("/?preview=1#/job/job-1/drawings");
  const reading = page.locator('[data-stage="pages"]');
  await expect(reading).toContainText("3 of 38 pages read");
  fail = true;
  await page.clock.runFor(5000);
  await expect(page.locator(".job-status")).toContainText("Unable to refresh status");
  await expect(reading.getByRole("button", { name: "Restart this step" })).toBeVisible();
  fail = false;
  await page.clock.runFor(5000);
  await expect(page.locator(".job-status")).toContainText("3 of 38 pages read");
});

test("an out-of-date Phase 1 result says it is redone after the review, not that Results has exceptions", async ({
  page,
}) => {
  await reviewFinished(page, [{ status: "done", phase: "phase1_base_room" }]);
  const calculation = page.locator('[data-stage="calculation"]');
  await expect(calculation).toContainText("The empty-room load is recalculated when the drawing review finishes.");
  await expect(page.getByText("on Results")).toHaveCount(0);
});

test("a refused AI key stops page reading with a plain reason and Try again", async ({
  page,
}) => {
  await preview(page, { status: { stage: "drawings" } });
  const posts = [];
  await page.route("**/api/page-reading**", async (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { status: "queued" } });
    }
    return route.fulfill({
      json: {
        status: "failed", recovery: { action: "resume", label: "Try again" },
        page_count: 73,
        problem:
          'None of 73 pages could be read (pages 1, 2, 3). First reason: The openrouter API returned HTTP 401: {"error":{"message":"API key expired.","code":401}} Retry to read them.',
      },
    });
  });
  await page.goto("/?preview=1#/job/job-1/drawings");
  const reading = page.locator(".job-stage", { hasText: "Read every page" });
  await expect(reading).toContainText(
    "The AI key on this computer was refused (it may have expired).",
  );
  await expect(page.getByText("HTTP 401")).toHaveCount(0);
  await expect(page.locator("[role=status]").first()).toContainText(
    "The AI key on this computer was refused",
  );
  await reading.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ project_id: "job-1", action: "resume" });
});

test("Rooms shows per-room Phase 1 evidence without asking anyone to draw", async ({
  page,
}) => {
  await preview(page);
  await page.goto("/?preview=1#/job/job-1/rooms/shop");
  await expect(
    page.getByRole("heading", { name: "What the load is worked out from" }),
  ).toBeVisible();
  await expect(page.getByText("1 wall to outside")).toBeVisible();
  await expect(
    page.getByText("Assumed ground slab from the drawing."),
  ).toBeVisible();
  await expect(page.getByText("Working out walls and roof")).toBeVisible();
  await expect(page.getByText(/draw a room|trace a room/i)).toHaveCount(0);
  await expect(page.locator(".job-room-table tr.selected")).toBeVisible();
});

test("Correct area saves through the existing override and recalculates Phase 1", async ({
  page,
}) => {
  await preview(page);
  let saved, calculated;
  await page.route("**/api/room-area-override", async (route) => {
    saved = route.request().postDataJSON();
    await route.fulfill({ json: {} });
  });
  await page.route("**/api/job-calculation", async (route) => {
    if (route.request().method() === "POST") {
      calculated = route.request().postDataJSON();
      await route.fulfill({
        json: { status: "done", phase: "phase1_base_room" },
      });
    } else
      await route.fulfill({
        json: { status: "done", phase: "phase1_base_room" },
      });
  });
  await page.goto("/?preview=1#/job/job-1/rooms/shop");
  await page.getByRole("button", { name: "Correct the area" }).click();
  const input = page.getByLabel("Correct the area of Shop");
  await input.fill("53.2");
  await page.getByRole("button", { name: "Save area" }).click();
  await expect.poll(() => saved?.area_m2).toBe(53.2);
  await expect.poll(() => calculated?.phase).toBe("phase1_base_room");
});

test("ceiling height uses the existing override and wall edits use envelope classification", async ({
  page,
}) => {
  await preview(page);
  let height,
    classification,
    calculationCount = 0;
  await page.route("**/api/room-height-override", async (route) => {
    height = route.request().postDataJSON();
    await route.fulfill({ json: {} });
  });
  await page.route("**/api/reviewer-room-geometry", async (route) => {
    classification = route.request().postDataJSON();
    await route.fulfill({ json: {} });
  });
  await page.route("**/api/job-calculation", async (route) => {
    if (route.request().method() === "POST") calculationCount++;
    await route.fulfill({
      json: { status: "done", phase: "phase1_base_room" },
    });
  });
  await page.goto("/?preview=1#/job/job-1/rooms/shop");
  await page
    .getByRole("button", { name: "Change", exact: true })
    .first()
    .click();
  await page.getByLabel("Ceiling height in metres").fill("3.2");
  await page.getByRole("button", { name: "Save and update Phase 1" }).click();
  await expect.poll(() => height?.ceiling_height_mm).toBe(3200);
  await expect.poll(() => calculationCount).toBe(1);
  await page.goto("/?preview=1#/job/job-1/rooms/shop");
  await page.getByRole("button", { name: "Change a wall or the roof" }).click();
  await page.locator("select[name='edge-0']").selectOption("mall");
  await page.getByRole("button", { name: "Save and update Phase 1" }).click();
  await expect.poll(() => classification?.edges?.[0]?.boundary).toBe("mall");
  await expect.poll(() => calculationCount).toBe(2);
});

test("Results stay locked before Phase 2 is complete", async ({ page }) => {
  await preview(page);
  await page.goto("/?preview=1#/job/job-1/results");
  await expect(
    page.getByRole("heading", { name: "Rooms and operating loads" }),
  ).toBeVisible();
  await expect(page.getByText("4.2", { exact: true })).toHaveCount(0);
});

test("Operating loads show room inputs and sources without blocking typical values", async ({
  page,
}) => {
  await preview(page);
  await page.goto("/?preview=1#/job/job-1/operating");
  await expect(
    page.getByRole("heading", { name: "Rooms and operating loads" }),
  ).toBeVisible();
  await expect(page.getByText("People", { exact: true })).toBeVisible();
  await expect(page.getByText("Typical value", { exact: true })).toBeVisible();
  await expect(
    page.getByText("No required operating-load answers are waiting."),
  ).toBeVisible();
});

test("an equipment card shows its schedule code, the printed quantity and how plan labels were tied to it", async ({
  page,
}) => {
  await preview(page, { status: { phase1_complete: true } });
  await page.route("**/api/page-extraction**", (route) =>
    route.fulfill({
      json: {
        status: "done",
        findings: [
          {
            id: "eq-1", status: "accepted", pages: [5, 19], evidence: "supported",
            value: { code: "E21", name: "UB FRIDGE", quantity: 10 },
            schedule_notes: ["Also labelled on the plans as UB FR 1500 (page 19): the same scheduled item, counted once."],
          },
          {
            id: "eq-2", status: "proposed", pages: [19], evidence: "supported",
            value: { code: "", name: "UB FRIDGE", quantity: 1 },
            possibly_same_as: [{ id: "eq-1", label: "E21 UB FRIDGE" }, { id: "eq-3", label: "E02 UB FRIDGE" }],
          },
        ],
      },
    }),
  );
  await page.goto("/?preview=1#/job/job-1/operating");
  const cards = page.locator(".job-equipment-list li");
  await expect(cards.first()).toContainText("E21 UB FRIDGE × 10");
  await expect(cards.first().locator("[data-schedule-note]")).toHaveText(
    "Also labelled on the plans as UB FR 1500 (page 19): the same scheduled item, counted once.",
  );
  await expect(cards.nth(1).locator("[data-possible-duplicate]")).toHaveText(
    "Possibly the same as E21 UB FRIDGE or E02 UB FRIDGE; reject it if so.",
  );
});

test("Jobs and job details routes render the guided journey", async ({
  page,
}) => {
  await preview(page);
  await page.goto("/?preview=1#/jobs");
  await expect(page.getByRole("heading", { name: "Jobs" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New job" })).toBeVisible();
  await page.goto("/?preview=1#/job/job-1/project");
  await expect(
    page.getByRole("heading", { name: "Job details" }),
  ).toBeVisible();
  await expect(page.locator("#jobApp input[name=address]")).toHaveValue(
    "1 Main Street",
  );
});

test("current Results show the total, room bars and assumptions", async ({
  page,
}) => {
  await preview(page, {
    status: {
      phase1_complete: true,
      phase2_complete: true,
      results_ready: true,
      result_stale: false,
    },
    final_report: {
      included_scope_peak: {
        final_design_total_kw: 4.2,
        design_total_kw: 4.2,
        safety_factor: 1.1,
      },
      room_peaks: [
        { name: "Shop", design_total_kw: 4.2, components: { people: 1.2 } },
      ],
    },
    rooms: [
      {
        ...plan.rooms[0],
        state: "done",
        phase1_kw: 2.5,
        final_kw: 4.2,
        final_w_m2: 84,
      },
    ],
  });
  await page.route("**/api/projects", (route) =>
    route.fulfill({
      json: [{ ...project, phase2_complete: true, results_ready: true }],
    }),
  );
  await page.route("**/api/job-status**", (route) =>
    route.fulfill({
      json: {
        ...plan.status,
        phase1_complete: true,
        phase2_complete: true,
        results_ready: true,
        result_stale: false,
      },
    }),
  );
  await page.goto("/?preview=1#/job/job-1/results");
  await expect(
    page.getByRole("heading", { name: "Design cooling load for the tenancy" }),
  ).toBeVisible();
  await expect(page.locator(".job-total")).toContainText("4.2");
  await expect(page.locator(".job-stacked-bar")).toHaveAttribute(
    "aria-label",
    /People/,
  );
  await expect(page.getByText("Window shading is not resolved")).toBeVisible();
});

test("stale Results remain visible and offer a final-load update", async ({
  page,
}) => {
  await preview(page, {
    status: {
      phase1_complete: true,
      phase2_complete: true,
      results_ready: true,
      result_stale: true,
    },
    final_report: {
      included_scope_peak: {
        final_design_total_kw: 4.2,
        design_total_kw: 4.2,
        safety_factor: 1.1,
      },
      room_peaks: [{ name: "Shop", design_total_kw: 4.2 }],
    },
    rooms: [
      {
        ...plan.rooms[0],
        state: "done",
        phase1_kw: 2.5,
        final_kw: 4.2,
        final_w_m2: 84,
      },
    ],
  });
  await page.route("**/api/projects", (route) =>
    route.fulfill({
      json: [
        {
          ...project,
          phase2_complete: true,
          results_ready: true,
          result_stale: true,
        },
      ],
    }),
  );
  await page.route("**/api/job-status**", (route) =>
    route.fulfill({
      json: {
        ...plan.status,
        phase1_complete: true,
        phase2_complete: true,
        results_ready: true,
        result_stale: true,
      },
    }),
  );
  await page.goto("/?preview=1#/job/job-1/results");
  await expect(page.locator(".job-result-summary.stale")).toBeVisible();
  await expect(page.getByText("Out of date.", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Update the final load" }),
  ).toBeVisible();
});

test("locked Results are not keyboard links and the preview fits 375 pixels", async ({
  page,
}) => {
  await preview(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/?preview=1#/job/job-1/drawings");
  await expect(
    page.locator(".job-step.locked").filter({ hasText: "Results" }),
  ).toHaveCount(1);
  await expect(
    page.locator(".job-step.locked").filter({ hasText: "Results" }),
  ).not.toHaveAttribute("href", /.*/);
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBe(375);
  await page.keyboard.press("Tab");
  await expect
    .poll(() =>
      page.evaluate(
        () => getComputedStyle(document.activeElement).outlineStyle,
      ),
    )
    .not.toBe("none");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect
    .poll(() =>
      page
        .locator(".job-stage-icon")
        .first()
        .evaluate((el) => getComputedStyle(el, ":after").animationName),
    )
    .toBe("none");
});

test("New job is one large drop tile; a chosen PDF uploads, starts the server job and opens Drawings", async ({
  page,
}) => {
  await preview(page);
  let started;
  await page.route("**/api/upload", (route) =>
    route.fulfill({ json: { id: "new-job", pages: 2 } }),
  );
  await page.route("**/api/new-job**", async (route) => {
    if (route.request().method() === "POST") {
      started = route.request().postDataJSON();
      return route.fulfill({ json: { id: "new-job", state: "running" } });
    }
    return route.fulfill({
      json: { id: "new-job", state: "running", analysed: false, drawings_ready: false,
              step_label: "Reading the PDF: page titles, drawing types and previews" },
    });
  });
  await page.goto("/?preview=1#/new-job");
  const tile = page.getByRole("button", { name: /Drop the PDF here/ });
  await expect(tile).toBeVisible();
  await expect(tile).toContainText("or click to choose it from your Downloads");
  const box = await tile.boundingBox();
  expect(box.height).toBeGreaterThanOrEqual(300);
  await page.locator("#job-pdf").setInputFiles({
    name: "sample.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4"),
  });
  await expect.poll(() => started?.project_id).toBe("new-job");
  await expect(page).toHaveURL(/#\/job\/new-job\/drawings/);
  const preparation = page.locator("[data-preparation-stage]");
  await expect(preparation).toContainText("Prepare the drawing set");
  await expect(preparation).toContainText("Reading the PDF: page titles");
  await expect(page.locator(".job-status")).toContainText("Toki is reading the PDF");
});

test("New job refuses a file that isn't a PDF and says why", async ({ page }) => {
  await preview(page);
  let uploads = 0;
  await page.route("**/api/upload", (route) => {
    uploads += 1;
    return route.fulfill({ json: { id: "x" } });
  });
  await page.goto("/?preview=1#/new-job");
  await page.locator("#job-pdf").setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("hello"),
  });
  await expect(page.getByRole("alert")).toContainText("notes.txt isn’t a PDF");
  expect(uploads).toBe(0);
});

test("a stopped PDF reading offers Try again, which restarts the server job", async ({ page }) => {
  await preview(page);
  const posts = [];
  await page.route("**/api/new-job**", async (route) => {
    if (route.request().method() === "POST") {
      posts.push(route.request().postDataJSON());
      return route.fulfill({ json: { state: "running" } });
    }
    return route.fulfill({
      json: { id: "job-1", state: "failed", recovery: { action: "resume", label: "Try again" }, analysed: false, drawings_ready: false,
              error: "Reading the PDF stopped before it finished (the server was restarted). Start it again." },
    });
  });
  await page.goto("/?preview=1#/job/job-1/drawings");
  const preparation = page.locator("[data-preparation-stage]");
  await expect(preparation).toContainText("stopped before it finished");
  await preparation.getByRole("button", { name: "Try again" }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ project_id: "job-1", action: "resume" });
});

test("failed preparation remains recoverable even when a drawing packet already exists", async ({ page }) => {
  await preview(page, { status: { phase1_complete: false } });
  await page.route("**/api/new-job**", route => route.fulfill({ json: { state: "failed", drawings_ready: true,
    recovery: { action: "resume", label: "Try again" }, error: "Preparing the checks stopped." } }));
  await page.goto("/?preview=1#/job/job-1/drawings");
  const preparation = page.locator("[data-preparation-stage]");
  await expect(preparation).not.toHaveClass(/done/);
  await expect(preparation.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("a prepared job shows the drawing set step as done", async ({ page }) => {
  await preview(page, { status: { stage: "rooms" } });
  await page.route("**/api/new-job**", (route) =>
    route.fulfill({ json: { id: "job-1", state: "done", analysed: true, drawings_ready: true, pages: 38 } }),
  );
  await page.goto("/?preview=1#/job/job-1/drawings");
  await expect(page.locator(".job-stage.done", { hasText: "Prepare the drawing set" })).toContainText("38 pages ready.");
  await expect(page.locator("[data-start-new-job]")).toHaveCount(0);
});

test("real plan fixture draws only clear outlines, hatches refrigeration, and lists the untraced Bar", async ({
  page,
}) => {
  await realContractPreview(page, needsAreaFixture);
  await page.goto(`/?preview=1#/job/${needsAreaFixture.status.id}/drawings`);
  await expect(page.locator(".job-plan image")).toHaveCount(0);
  await expect(
    page.locator(
      '.job-plan polygon[data-room-id="room-use:unassigned-level:coolroom"]',
    ),
  ).toHaveAttribute("fill", /refrigeration-/);
  await expect(
    page.locator(".job-plan defs pattern[id^='refrigeration-']"),
  ).toHaveCount(1);
  await page.goto(`/?preview=1#/job/${needsAreaFixture.status.id}/rooms`);
  const barRow = page.locator(".job-room-table tr").filter({ hasText: "Bar" });
  await expect(barRow).toContainText("No outline");
  await expect(barRow).toContainText("Type the area");
  await expect(page.locator(".job-plan image")).toHaveCount(0);
  await expect(page.locator(".job-plan polygon.assumed")).toHaveCount(0);
});

test("every documented room state is distinct and Needs you contains only actionable rooms", async ({
  page,
}) => {
  const fixture = copy(needsAreaFixture);
  const rooms = fixture.rooms;
  rooms.push(
    {
      room_id: "test-surfaces",
      name: "Surface room",
      level: "",
      area_m2: 20,
      area_label: "AI estimate from the plan",
      area_basis: "estimate",
      phase1_kw: null,
      final_kw: null,
      kind: "comfort",
      included: true,
      has_outline: false,
      state: "finding_surfaces",
      source_pages: [],
    },
    {
      room_id: "test-area-reading",
      name: "Area room",
      level: "",
      area_m2: null,
      area_label: "",
      area_basis: "",
      phase1_kw: null,
      final_kw: null,
      kind: "comfort",
      included: true,
      has_outline: false,
      state: "finding_area",
      source_pages: [],
    },
    {
      room_id: "test-use",
      name: "Unclear room",
      level: "",
      area_m2: 12,
      area_label: "Printed on the drawing",
      area_basis: "printed",
      phase1_kw: null,
      final_kw: null,
      kind: "comfort",
      included: true,
      has_outline: false,
      state: "needs_use",
      source_pages: [],
    },
    {
      room_id: "test-left",
      name: "Plant",
      level: "",
      area_m2: null,
      area_label: "",
      area_basis: "",
      phase1_kw: null,
      final_kw: null,
      kind: "not_a_room",
      included: false,
      has_outline: false,
      state: "left_out",
      source_pages: [],
    },
  );
  await realContractPreview(page, fixture);
  await page.goto(`/?preview=1#/job/${fixture.status.id}/rooms`);
  const states = [
    ["Kitchen", "Calculated"],
    ["Surface room", "Working out walls and roof"],
    ["Area room", "Working out its area"],
    ["Bar", "Type the area"],
    ["Unclear room", "Say what the room is used for"],
    ["Coolroom", "Not in this load"],
    ["Plant", "Left out"],
  ];
  for (const [name, state] of states) {
    await expect(
      page.locator(".job-room-table tr").filter({ hasText: name }),
    ).toContainText(state);
  }
  const needs = page.locator(".job-needs li");
  await expect(page.locator(".job-needs h2")).toHaveText("2 things need you");
  await expect(needs).toHaveCount(2);
  await expect(page.locator(".job-needs")).not.toContainText("Area room");
  await expect(page.locator(".job-needs")).not.toContainText("Surface room");
});

test("Phase 1 completion controls the Drawings calculation check and failure copy", async ({
  page,
}) => {
  const fixture = copy(doneFixture);
  fixture.status.phase1_complete = false;
  await realContractPreview(page, fixture);
  await page.goto(`/?preview=1#/job/${fixture.status.id}/drawings`);
  const calc = page
    .locator(".job-stage")
    .filter({ hasText: "Calculate each room’s empty-room load" });
  await expect(calc).not.toHaveClass(/done/);
  await page.route("**/api/skill-workflow**", (route) =>
    route.fulfill({
      json: {
        status: "failed", recovery: { action: "resume", label: "Try again" },
        remediation: "Internal evidence tasks need operator exceptions",
      },
    }),
  );
  await page.reload();
  const envelope = page
    .locator(".job-stage")
    .filter({ hasText: "Work out walls, roof, floor and windows" });
  await expect(envelope).toContainText(
    "Working out the walls, roof and floor stopped part-way. What it found so far is kept.",
  );
  await expect(
    envelope.getByRole("button", { name: "Try again" }),
  ).toBeVisible();
  await expect(envelope).not.toContainText("evidence tasks");
});

test("an operating load job that is running or already current never starts another full calculation", async ({
  page,
}) => {
  for (const state of ["running", "current"]) {
    const fixture = copy(doneFixture);
    fixture.status.phase1_complete = true;
    fixture.status.needs_you = { ...fixture.status.needs_you, phase2: 0 };
    fixture.status.results_ready = state === "current";
    fixture.status.result_stale = false;
    await realContractPreview(page, fixture);
    let posts = 0;
    await page.route("**/api/job-calculation", async (route) => {
      if (route.request().method() === "POST") posts++;
      await route.fulfill({
        json: { status: state === "running" ? "running" : "done" },
      });
    });
    if (state === "running") {
      await page.route("**/api/job-calculation**", async (route) => {
        if (route.request().method() === "POST") posts++;
        await route.fulfill({ json: { status: "running" } });
      });
    }
    await page.goto(`/?preview=1#/job/${fixture.status.id}/operating`);
    await page.waitForTimeout(100);
    expect(posts).toBe(0);
  }
});

test("Results use room-plan areas and loads and shade by final W/m²", async ({
  page,
}) => {
  const fixture = copy(doneFixture);
  fixture.status.phase1_complete = true;
  fixture.status.phase2_complete = true;
  fixture.status.results_ready = true;
  fixture.status.result_stale = false;
  fixture.final_report.included_scope_peak = {
    final_design_total_kw: 9.6,
    design_total_kw: 8.7,
    safety_factor: 1.1,
  };
  fixture.final_report.room_peaks = [
    { name: "Bar", design_total_kw: 99, components: { people: 1.5 } },
  ];
  const bar = fixture.rooms.find((room) => room.name === "Bar");
  bar.final_kw = 2.4;
  bar.final_w_m2 = 196.7;
  await realContractPreview(page, fixture);
  await page.goto(`/?preview=1#/job/${fixture.status.id}/results`);
  const row = page.locator(".job-results-table tr").filter({ hasText: "Bar" });
  await expect(row).toContainText("12.2");
  await expect(row).toContainText("2.4 kW");
  await expect(row).not.toContainText("99 kW");
  await expect(
    page.locator(".job-plan text").filter({ hasText: "197 W/m²" }),
  ).toHaveCount(1);
});

test("a stale report explains its old total without claiming that zero rooms were counted", async ({
  page,
}) => {
  const fixture = copy(doneFixture);
  fixture.status.phase1_complete = false;
  fixture.status.phase2_complete = false;
  fixture.status.results_ready = true;
  fixture.status.result_stale = true;
  fixture.final_report.included_scope_peak = {
    final_design_total_kw: 33.8,
    design_total_kw: 30.7,
    safety_factor: 1.1,
    display_hour: 14,
  };
  fixture.final_report.room_peaks = [
    {
      name: "Bar",
      design_total_kw: 3.7,
      components: { outside_air: 1.2, people: 0.5 },
    },
    { name: "Kitchen", design_total_kw: 12.2 },
    { name: "Shop", design_total_kw: 14.8 },
  ];
  fixture.rooms.forEach((room) => {
    if (room.kind === "comfort") {
      room.phase1_kw = null;
      room.final_kw = null;
      room.state = "finding_surfaces";
    }
  });
  await realContractPreview(page, fixture);
  await page.goto(`/?preview=1#/job/${fixture.status.id}/results`);
  await expect(page.locator(".job-total")).toContainText("33.8");
  await expect(page.locator(".job-result-summary")).toContainText(
    "previous result covered 3 rooms",
  );
  await expect(page.locator(".job-result-summary")).not.toContainText(
    "0 rooms counted",
  );
  await expect(
    page.getByRole("link", { name: "Review room inputs before updating" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Update the final load" }),
  ).toHaveCount(0);
  await expect(page.locator(".job-results-table")).toContainText("12.2");
  await expect(page.locator(".job-results-table")).toContainText(
    "Not calculated yet",
  );
  await expect(
    page.getByRole("img", { name: /Past breakdown, out of date/ }),
  ).toHaveCount(3);
  await expect(page.locator("#jobApp")).not.toContainText(
    /\bfinding\b|\bskill\b/i,
  );
});

test("Jobs uses the display name, site, and upload date", async ({ page }) => {
  await realContractPreview(page, doneFixture);
  await page.goto("/?preview=1#/jobs");
  const row = page.locator(".job-jobs-table tbody tr").first();
  await expect(row).toContainText("Butcher Buffet at Melrose Park");
  await expect(row).toContainText("TENANCY MZ01,M38, MELROSE CENTRAL");
  await expect(row).not.toContainText("original filename");
  await expect(row).toContainText("12 Oct 2026");
});

test("every preview screen fetches one job plan and displays no internal process terms", async ({
  page,
}) => {
  const forbidden =
    /unassigned level|\bfinding\b|\bskill\b|supported area source/i;
  for (const fixtureSource of [doneFixture, needsAreaFixture]) {
    const fixture = copy(fixtureSource);
    fixture.status.phase1_complete = true;
    fixture.status.phase2_complete = true;
    fixture.status.results_ready = true;
    fixture.status.needs_you = { ...fixture.status.needs_you, phase2: 1 };
    fixture.final_report.included_scope_peak = {
      final_design_total_kw: 0,
      design_total_kw: 0,
      safety_factor: 1.1,
    };
    fixture.rooms.forEach((room) => {
      if (room.phase1_kw == null && room.kind === "comfort" && room.included)
        room.phase1_kw = 0;
      if (room.final_kw == null && room.kind === "comfort" && room.included)
        room.final_kw = 0;
    });
    for (const step of ["drawings", "rooms", "operating", "results"]) {
      await realContractPreview(page, fixture);
      let planRequests = 0;
      await page.route("**/api/job-plan**", async (route) => {
        planRequests++;
        await route.fulfill({ json: fixture });
      });
      await page.goto(`/?preview=1#/job/${fixture.status.id}/${step}`);
      if (
        step === "results" &&
        !fixture.final_report.included_scope_peak.final_design_total_kw
      ) {
        await expect(page.locator(".job-result-summary")).toBeVisible();
      }
      await expect.poll(() => planRequests).toBe(1);
      await expect(page.locator("#jobApp")).not.toContainText(forbidden);
    }
  }
});
