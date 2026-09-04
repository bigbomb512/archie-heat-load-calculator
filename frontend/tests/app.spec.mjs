import { expect, test } from "@playwright/test";

const analysis = {
  id: "demo-project",
  name: "Demo drawing set.pdf",
  pages_analysed: 2,
  relevant_count: 1,
  selected_count: 1,
  warnings: [],
  sheets: [
    {
      page: 1,
      type: "floor_plan",
      title: "Ground Floor Plan",
      reason: "Matched drawing title: floor plan.",
      confidence: 0.94,
      relevant: true,
      selected_by_default: true,
      packet_role: "",
      plan_role: "main_floor_plan",
      kept_for_review: true,
      review_bucket: "primary",
      scale: "1:100",
      dimension_count: 8,
      room_count: 2,
      level_name: "Ground Floor",
      level_status: "detected",
      visual: {},
      thumbnail: "",
    },
  ],
};

async function mockApi(page) {
  await page.route("**/api/projects", route => route.fulfill({ json: [
    { id: "demo-project", name: analysis.name, pages: 2, relevant: 1, analysed: true },
  ] }));
  await page.route("**/api/upload", route => route.fulfill({ json: {
    id: "demo-project", name: analysis.name, pages: 2, size_bytes: 1024,
  } }));
  await page.route("**/api/analyse", route => route.fulfill({ json: analysis }));
  await page.route("**/api/analysis?id=demo-project", route => route.fulfill({ json: analysis }));
}

const designRequirements = {
  space_usage: "Retail tenancy",
  occupancy: 12,
  operating_hours: "Mon-Fri 08:00-18:00",
  indoor_cooling_setpoint_c: 24,
  indoor_heating_setpoint_c: 20,
  outdoor_summer_db_c: 35,
  outdoor_winter_db_c: 5,
  fresh_air_basis: "AS 1668 basis",
  exhaust_basis: "No process exhaust is required.",
  cooking_activity: "none",
  hood_requirement: "not_required",
  exhaust_outcome: "not_required",
  make_up_air_requirement: "not_required",
  ceiling_height_mm: 3200,
  ceiling_void_height_mm: 400,
  heat_sources: [],
  zones: [],
  existing_services: "Existing services confirmed.",
  service_constraints: {},
  code_basis: "NCC and AS 1668",
  verification: {
    occupancy: { status: "confirmed", source: "Client brief" },
    design_conditions: { status: "confirmed", source: "Designer basis" },
    outside_air: { status: "confirmed", source: "AS 1668" },
    exhaust: { status: "not_applicable", source: "Client confirmation" },
    heat_sources: { status: "missing", source: "" },
    ceiling: { status: "confirmed", source: "Architectural plan" },
    existing_services: { status: "confirmed", source: "Site survey" },
  },
};

const coolingRequirements = {
  ...designRequirements,
  cooling_load_conditions: {
    indoor_cooling_wet_bulb_c: 18,
    outdoor_summer_wet_bulb_c: 24,
    atmospheric_pressure_kpa: 101.325,
    verification_status: "confirmed",
    source: "Designer summer basis",
  },
  zones: [{
    zone_id: "zone_001", name: "Sales area", usage: "Retail sales", source_room_labels: ["Sales"],
    area_m2: 30, occupancy: 18, heat_sources: [{ name: "Display fridge", kind: "refrigeration", quantity: 1, watts: 700, diversity_factor: 1, space_gain_factor: 0.8, verification_status: "confirmed", source: "Manufacturer schedule" }],
    cooling_load: {
      people_sensible_w_per_person: 75, people_latent_w_per_person: 55, people_diversity_factor: 0.8,
      lighting_w_m2: 10, lighting_diversity_factor: 0.9, outside_air_lps: 90, safety_factor: 1.1,
      envelope_not_applicable: true, envelope_surfaces: [], verification_status: "confirmed", source: "Designer calculation basis",
    },
  }],
};

const ventilationRequirements = {
  ...designRequirements,
  zones: [{
    zone_id: "zone_001", name: "Sales area", usage: "Retail sales", source_room_labels: ["Sales"], area_m2: 30, occupancy: 18,
    ventilation_requirements: {
      process_type: "none", basis_name: "Project ventilation basis", basis_source: "Designer record",
      outside_air_method: "combined", people_rate_lps_per_person: 5, area_rate_lps_per_m2: 2, fixed_minimum_lps: null,
      process_exhaust_requirement: "not_required", process_exhaust_lps: null, hood_type_or_duty: "", recirculable: "yes",
      allowable_transfer_air_lps: 0, allowable_outside_air_credit_lps: 0,
      design_supply_lps_including_outside_air: 100, return_or_relief_lps: 100, dedicated_make_up_air_lps: 0,
      verification_status: "confirmed", source: "Designer ventilation basis",
    },
  }],
};

test("design-input verification controls save with the reasoning packet", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  await page.route("**/api/design-requirements**", async route => {
    const request = route.request();
    if (request.method() === "GET") {
      return route.fulfill({ json: { id: "demo-project", requirements: designRequirements, readiness: {
        status: "brief_allowed", missing_inputs: ["major appliance/equipment heat loads"], provisional_inputs: [], input_errors: [],
      }, room_suggestions: [{ label: "Sales area", area: "30m2", source_page: 1 }] } });
    }
    const body = request.postDataJSON();
    expect(body.requirements.occupancy).toBe(18);
    expect(body.requirements.verification.occupancy.status).toBe("confirmed");
    expect(body.requirements.ceiling_height_mm).toBe(3200);
    expect(body.requirements.zones).toEqual(expect.arrayContaining([expect.objectContaining({
      zone_id: "zone_001", name: "Sales area", usage: "Retail sales", area_m2: 30, occupancy: 18,
    })]));
    return route.fulfill({ json: {
      requirements: { ...designRequirements, occupancy: 18 },
      requirements_readiness: { status: "brief_allowed", missing_inputs: ["major appliance/equipment heat loads"], provisional_inputs: [], input_errors: [] },
      requirements_url: "/output/design_requirements.json",
      reasoning_zip_url: "/output/reasoning_packet.zip",
    } });
  });

  await page.goto("/");
  await page.evaluate(() => { show("vRes"); showDesignRequirements({}, {}, [{ label: "Sales area", area: "30m2", source_page: 1 }]); });
  await page.locator("#reqOccupancy").fill("18");
  await page.locator("#reqOccupancyStatus").selectOption("confirmed");
  await page.locator("#reqOccupancySource").fill("Client brief");
  await page.locator("#reqCeilingHeight").fill("3200");
  await page.locator(".zone-editor summary").click();
  await page.locator(".zone-usage").fill("Retail sales");
  await page.locator(".zone-occupancy").fill("18");
  await page.locator(".zone-add-heat").click();
  await page.locator(".zone-heat-name").fill("Display fridge");
  await page.locator(".zone-heat-quantity").fill("1");
  await page.locator(".zone-heat-watts").fill("700");
  await page.locator(".zone-heat-note").fill("Manufacturer schedule");
  await page.locator(".zone-heat-status").selectOption("confirmed");
  await page.evaluate(() => { DATA = { id: "demo-project" }; });
  await page.locator("#btnSaveRequirements").click();

  await expect(page.locator("#requirementsLinks")).toContainText("refreshed reasoning packet");
  expect(errors).toEqual([]);
});

test("cooling-load calculation displays a deterministic zone breakdown", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  await page.route("**/api/heat-load", async route => {
    const body = route.request().postDataJSON();
    expect(body.requirements.zones[0].cooling_load.outside_air_lps).toBe(90);
    return route.fulfill({ json: {
      heat_load_status: "current",
      heat_load_report_url: "/output/heat_load_report.json",
      reasoning_zip_url: "/output/reasoning_packet.zip",
      heat_load_report: {
        status: "calculated", calculated_zone_count: 1, blocked_zone_count: 0, project_total_kw: 4.2,
        zone_results: [{ zone_name: "Sales area", status: "calculated", subtotal_kw: 3.8, safety_allowance_kw: 0.4, design_total_kw: 4.2,
          contributions: [{ name: "people", total_kw: 1.1 }, { name: "outside_air", total_kw: 1.4 }],
        }],
      },
    } });
  });

  await page.goto("/");
  await page.evaluate(requirements => {
    DATA = { id: "demo-project" };
    show("vRes");
    showDesignRequirements(requirements, {}, []);
  }, coolingRequirements);
  await page.locator("#btnCalculateHeatLoad").click();

  await expect(page.locator("#heatLoadStatus")).toContainText("Project total 4.20 kW");
  await expect(page.locator("#heatLoadResults")).toContainText("outside_air: 1.40 kW");
  expect(errors).toEqual([]);
});

test("ventilation calculation displays outside-air and exhaust evidence", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);
  await page.route("**/api/ventilation", async route => {
    const body = route.request().postDataJSON();
    expect(body.requirements.zones[0].ventilation_requirements.people_rate_lps_per_person).toBe(5);
    return route.fulfill({ json: {
      ventilation_status: "current",
      ventilation_report_url: "/output/ventilation_report.json",
      reasoning_zip_url: "/output/reasoning_packet.zip",
      ventilation_report: {
        status: "calculated", calculated_zone_count: 1, blocked_zone_count: 0,
        total_outside_air_lps: 90, total_process_exhaust_lps: 0,
        zone_results: [{ zone_name: "Sales area", status: "calculated", process_exhaust_lps: 0,
          outside_air: { required_lps: 90, governing_component: "occupancy" },
          make_up_air: { required_lps: 0 }, air_balance: { status: "evaluated", net_lps: 0 }, warnings: [],
        }],
      },
    } });
  });

  await page.goto("/");
  await page.evaluate(requirements => {
    DATA = { id: "demo-project" };
    show("vRes");
    showDesignRequirements(requirements, {}, []);
  }, ventilationRequirements);
  await page.locator("#btnCalculateVentilation").click();

  await expect(page.locator("#ventilationStatus")).toContainText("Outside air 90.0 L/s");
  await expect(page.locator("#ventilationResults")).toContainText("Make-up air 0.0 L/s");
  expect(errors).toEqual([]);
});

test("analysis reaches results without browser errors", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") errors.push(message.text());
  });
  await mockApi(page);

  await page.goto("/");
  await page.locator("#pdf").setInputFiles({
    name: "demo.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%%EOF"),
  });
  await expect(page.locator("#btnAnalyse")).toBeVisible();
  await page.locator("#btnAnalyse").click();

  await expect(page.locator("#summaryTitle")).toHaveText("Ready for ChatGPT packet");
  await expect(page.locator("#btnConfirm")).toBeEnabled();
  await expect(page.locator("#btnConfirmTop")).not.toHaveClass(/hide/);
  expect(errors).toEqual([]);
});

test("saved project opens into results", async ({ page }) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockApi(page);

  await page.goto("/");
  await page.locator("[data-open='demo-project']").click();

  await expect(page.locator("#summaryTitle")).toHaveText("Ready for ChatGPT packet");
  await expect(page.locator("#btnConfirm")).toBeEnabled();
  expect(errors).toEqual([]);
});

test("frontend assets do not cache and accept query strings", async ({ request }) => {
  const home = await request.get("/");
  const script = await request.get("/frontend/js/app.js?smoke=1");

  expect(home.headers()["cache-control"]).toContain("no-store");
  expect(script.ok()).toBeTruthy();
  expect(script.headers()["cache-control"]).toContain("no-store");
});
