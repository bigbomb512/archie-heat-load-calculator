import { expect, test } from "@playwright/test";

test.beforeEach(async ({page})=>{
  await page.addInitScript(()=>{
    const url=new URL(location.href);url.searchParams.set("guided","1");history.replaceState(null,"",url);
  });
});

const job = {id:"job-1", name:"Corner cafe", pages:2, analysed:true, updated_at:"2026-10-16",stage:"phase1",needs_you:{phase1:1}};
const status = (over={}) => ({id:"job-1",name:"Corner cafe",address:"1 Main Street",found_site:"",drawings_confirmed:true,
  stage:"phase1",phase1_complete:false,phase2_complete:false,results_ready:false,result_stale:false,
  needs_you:{drawings:0,phase1:1,phase2:0,results:0},checks:{waiting:0,blocked:0},rooms:{total:1,included:1,with_area:1},
  room_heights:{"room-use:ground:shop":{"ceiling_height_mm":3000,"origin":"pdf_evidence"}},...over});
const model = {room_scope:{status:"confirmed",candidates:[{key:"room-use:ground:shop",label:"Shop",area_m2:42,area_origin:"pdf_evidence",include:true,status:"calculated"}]},
  phase1_base_room_load_report:{status:"draft",blocked_reasons:[],phase1_coverage:{complete:false,rooms_included:1,rooms_left_out:0},
    room_peaks:[{room_id:"room-use:ground:shop",name:"Shop",design_total_kw:3.2}],
    phase1_surface_audit:[{room_id:"room-use:ground:shop",label:"Outside wall",area_m2:18,u_value_w_m2k:1.4,boundary_temperature_c:35,total_kw:0.3}],
    phase1_assumptions:[{room_id:"room-use:ground:shop",room_name:"Shop",description:"Ceiling height from the drawing"}]},
  phase1_base_room_load_report_stale:false,hourly_ai_preliminary_load_report:{}};

async function mockBase(page){
  await page.route("**/api/projects",r=>r.fulfill({json:[job]}));
  await page.route("**/api/job-status**",r=>r.fulfill({json:status()}));
  await page.route("**/api/analysis?id=job-1",r=>r.fulfill({json:{...job,has_reasoning_packet:true,has_reasoning_packet:true}}));
  await page.route("**/api/ai-preliminary-model**",r=>r.fulfill({json:model}));
  await page.route("**/api/prepare-pages**",r=>r.fulfill({json:{status:"done"}}));
  await page.route("**/api/page-reading**",r=>r.request().method()==="GET"?r.fulfill({json:{status:"done",enabled:true,read:2,page_count:2}}):r.fulfill({json:{status:"done"}}));
  await page.route("**/api/page-extraction**",r=>r.fulfill({json:{status:"done",findings:[],open:0}}));
  await page.route("**/api/drawing-tasks**",r=>r.fulfill({json:{status:"done"}}));
  await page.route("**/api/skill-workflow**",r=>r.fulfill({json:{status:"complete",findings:[]}}));
  await page.route("**/api/job-calculation**",r=>r.fulfill({json:{status:"none"}}));
}

test("Jobs home is the normal entry and opens a saved job",async({page})=>{
  await mockBase(page);await page.goto("/");
  await expect(page.getByRole("heading",{name:"Jobs"})).toBeVisible();
  await expect(page.getByText("Phase 1 · 1 need you")).toBeVisible();
  await expect(page.getByRole("link",{name:/Corner cafe/})).toHaveAttribute("href",/#\/job\/job-1\/phase1/);
  await expect(page.locator(".shell")).toBeHidden();
});

test("Phase 1 is room based and the surface audit is visible",async({page})=>{
  await mockBase(page);await page.goto("/#/job/job-1/rooms");
  await expect(page.getByRole("heading",{name:"Step 2 · Phase 1 · Rooms"})).toBeVisible();
  await expect(page.getByText("Outside wall")).toBeVisible();
  await expect(page.locator(".gj-room-card").getByText("Ceiling height from the drawing")).toBeVisible();
  await expect(page.getByRole("link",{name:/Results/})).toHaveAttribute("aria-disabled","true");
  await expect(page.getByText(/People, lights, equipment and air are added in Phase 2/)).toBeVisible();
});

test("old Results route redirects before rendering any result numbers",async({page})=>{
  await mockBase(page);await page.goto("/#/job/job-1/results");
  await expect(page).toHaveURL(/#\/job\/job-1\/rooms/);
  await expect(page.getByText("Final design cooling load")).toHaveCount(0);
});

test("drawings show stopped work and translate internal review errors",async({page})=>{
  await mockBase(page);
  await page.route("**/api/skill-workflow**",route=>route.fulfill({json:{status:"failed",problem:"Skills read the pages only after every page has been read (page reading on, and the AI provider set up on this server)."}}));
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator(".gj-status")).toContainText("Working through the drawings stopped");
  await expect(page.locator(".gj-status")).toContainText("available AI connection");
  await expect(page.locator(".gj-timeline")).not.toContainText("Skills");
  await expect(page.getByRole("button",{name:"Try again"})).toBeVisible();
});

test("page reading off offers the action that restores the drawing workflow",async({page})=>{
  let enabled=false;const posts=[];
  await mockBase(page);
  await page.unroute("**/api/page-reading**");
  await page.route("**/api/page-reading**",route=>{
    if(route.request().method()==="GET")return route.fulfill({json:{status:"none",enabled}});
    const body=route.request().postDataJSON();posts.push(body);if(body.action==="set_enabled")enabled=true;
    return route.fulfill({json:{status:"queued",enabled}});
  });
  await page.goto("/#/job/job-1/drawings");
  await expect(page.locator(".gj-status")).toContainText("Page reading is turned off");
  await page.getByRole("button",{name:"Turn on and read pages"}).click();
  await expect.poll(()=>posts.length).toBe(2);
  expect(posts).toEqual([{project_id:"job-1",action:"set_enabled",enabled:true},{project_id:"job-1",action:"start",requested_by:"Operator"}]);
});

test("guided layout fits 375 pixels and supports theme switching",async({page})=>{
  await page.setViewportSize({width:375,height:812});await mockBase(page);await page.goto("/");
  await expect(page.getByRole("heading",{name:"Jobs"})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.getByRole("button",{name:"Switch theme"}).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme",/light|dark/);
});

test("Phase 1 missing drawing details can be answered inline with a source",async({page})=>{
  await mockBase(page);
  let answer;
  await page.unroute(/\/api\/skill-workflow/);
  await page.route(/\/api\/skill-workflow/,async route=>{
    if(route.request().method()==="POST"){answer=route.request().postDataJSON();return route.fulfill({json:{status:"complete"}});}
    return route.fulfill({json:{status:"needs_review",answers:{},findings:[{id:"information_needs:needs:1",subskill_id:"information_needs",field:"needs",status:"proposed",value:{target:"Shop wall",answer_kind:"construction",room:"Shop",why:"The wall build-up is not stated."}}]}});
  });
  await page.route("**/api/job-calculation",route=>route.fulfill({json:{status:"running"}}));
  await page.goto("/#/job/job-1/rooms");
  await expect(page.getByText("Needs you (1)")).toBeVisible();
  const need=page.locator(".gj-need-item").filter({hasText:"Shop wall"});
  await need.getByLabel("Answer").fill("Masonry wall, U-value 1.8");
  await need.getByLabel("Source").selectOption("spec_sheet");
  await need.getByRole("button",{name:"Save answer"}).click();
  await expect.poll(()=>answer).toMatchObject({action:"answer_need",finding_id:"information_needs:needs:1",kind:"construction",value:"Masonry wall, U-value 1.8",source:"spec_sheet",room:"Shop"});
});

test("job menu exports the saved job",async({page})=>{
  await mockBase(page);
  await page.route("**/api/project-export",route=>route.fulfill({json:{status:"current",artifact_url:"/api/artifacts/job-1.zip"}}));
  await page.goto("/#/job/job-1/rooms");
  await page.locator(".gj-menu summary").click();
  await page.getByRole("button",{name:"Export job"}).click();
  await expect(page.getByRole("link",{name:"Download export"})).toHaveAttribute("href","/api/artifacts/job-1.zip");
});
