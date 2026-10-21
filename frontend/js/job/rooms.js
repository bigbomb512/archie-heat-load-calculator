import { getJson, sendJson, escapeHtml, fmt, navigate } from "./api.js";
import { planSvg, lineLegend } from "./plan.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
const ESTIMATED_AREAS = new Set(["ai_outline", "remainder", "estimate"]);
const ASSUMED_AREAS = new Set(["sheet_scale", "printed_dimensions"]);

function norm(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function roomSurfaces(plan, room) {
  return (plan.phase1_report?.phase1_surface_audit || []).filter(
    (row) =>
      row.room_id === room.room_id || norm(row.room_name) === norm(room.name),
  );
}

function roomAssumptions(plan, room) {
  return (plan.phase1_report?.phase1_assumptions || []).filter(
    (row) =>
      row.room_id === room.room_id || norm(row.room_name) === norm(room.name),
  );
}

function sourceOf(surface) {
  const label = String(surface.label || "");
  const match = label.match(/\bto\s+(.+)$/i);
  if (match) return match[1].replace(/[.,;]+$/g, "");
  return (
    {
      external: "outside",
      adjacent_tenancy: "neighbouring tenancies",
      mall: "conditioned mall",
      conditioned_space: "conditioned space",
      ground: "ground",
    }[surface.boundary_condition] ||
    String(surface.surface_type || "envelope surface").replaceAll("_", " ")
  );
}

function groupSurfaces(surfaces) {
  const groups = new Map();
  for (const surface of surfaces) {
    const type = String(surface.surface_type || "");
    const face = type.includes("wall")
      ? sourceOf(surface)
      : type.includes("window") || type.includes("glazing")
        ? "Shopfront glazing"
        : type.includes("floor")
          ? "Floor slab"
          : type.includes("roof")
            ? "Roof"
            : type.includes("ceiling")
              ? "Ceiling"
              : sourceOf(surface);
    const key = `${type.includes("wall") ? "wall" : type}:${face}`;
    if (!groups.has(key)) {
      groups.set(key, {
        face,
        type,
        count: 0,
        area: 0,
        weightedU: 0,
        kw: 0,
        surfaces: [],
      });
    }
    const group = groups.get(key);
    const area = Number(surface.area_m2) || 0;
    group.count += 1;
    group.area += area;
    if (Number.isFinite(Number(surface.u_value_w_m2k))) {
      group.weightedU += area * Number(surface.u_value_w_m2k);
    }
    group.kw += Number(surface.total_kw) || 0;
    group.surfaces.push(surface);
  }
  return [...groups.values()].map((group) => ({
    ...group,
    u: group.area ? group.weightedU / group.area : null,
  }));
}

function surfaceTitle(group) {
  if (group.type.includes("wall")) {
    return `${group.count} wall${group.count === 1 ? "" : "s"} to ${group.face}`;
  }
  return group.face;
}

function beyondText(group) {
  const surface = group.surfaces[0] || {};
  const temperature = surface.boundary_temperature_c;
  const beyond = surface.boundary_source || "Beyond it is unresolved";
  return `${beyond}${temperature == null ? "" : ` ${fmt(temperature)} °C`}`;
}

function roomState(room) {
  if (room.kind === "refrigeration" || room.state === "sized_separately") {
    return `<span class="job-refrig">Not in this load <small>Refrigeration</small></span>`;
  }
  return (
    {
      done: "Calculated",
      finding_surfaces: "Working out walls and roof",
      finding_area: "Working out its area",
      needs_area: "Type the area",
      needs_use: "Say what the room is used for",
      left_out: "Left out",
    }[room.state] || "Not calculated yet"
  );
}

function roomRows(plan, selected) {
  return (plan.rooms || [])
    .map((room) => {
      const selectedClass = room.room_id === selected.room_id ? "selected" : "";
      const area = room.area_m2 == null ? "—" : `${fmt(room.area_m2)} m²`;
      const load = room.phase1_kw == null ? "—" : `${fmt(room.phase1_kw)} kW`;
      const noOutline = !room.has_outline
        ? `<small class="job-row-source">No outline</small>`
        : "";
      const source = room.area_label
        ? `<small class="job-row-source">${escapeHtml(room.area_label)}</small>`
        : "";
      return `<tr class="${selectedClass}">
        <th scope="row"><button class="job-room-select" data-room="${escapeHtml(room.room_id)}" aria-current="${room.room_id === selected.room_id ? "true" : "false"}">${escapeHtml(room.name)}</button>${source}${noOutline}</th>
        <td>${area}</td>
        <td>${load}</td>
        <td>${roomState(room)}</td>
      </tr>`;
    })
    .join("");
}

function areaClass(room) {
  if (ESTIMATED_AREAS.has(room.area_basis)) return "estimated";
  if (ASSUMED_AREAS.has(room.area_basis)) return "assumed";
  return "";
}

function surfaceRows(groups) {
  return groups
    .map(
      (group) => `<tr>
      <th scope="row">${escapeHtml(surfaceTitle(group))}</th>
      <td>${fmt(group.area)} m²</td>
      <td>${group.u == null ? "—" : fmt(group.u, 2)}</td>
      <td title="${escapeHtml(
        group.surfaces
          .map((surface) => surface.boundary_source || "")
          .filter(Boolean)
          .join("; "),
      )}">${escapeHtml(beyondText(group))}</td>
      <td>${fmt(group.kw)} kW</td>
    </tr>`,
    )
    .join("");
}

function useOptions(uses, room) {
  if (!uses.length) return "";
  return `<label class="job-field"><span>What is the room used for?</span>
    <select data-room-use="${escapeHtml(room.room_id)}"><option value="">Choose a use</option>
      ${uses
        .map(
          ([id, name]) =>
            `<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`,
        )
        .join("")}
    </select>
  </label>`;
}

function roomDetails(plan, room, status, uses, editor = {}) {
  const height = status.room_heights?.[room.room_id];
  const surfaces = roomSurfaces(plan, room);
  const assumptions = roomAssumptions(plan, room);
  const groups = groupSurfaces(surfaces);
  const walls = surfaces.filter((row) =>
    String(row.surface_type || "").includes("wall"),
  );
  if (editor.height) {
    return `<section class="job-panel job-room-detail" aria-labelledby="room-detail-title">
      <button class="job-link-button" data-back-room>Back to the room</button>
      <h2 id="room-detail-title">Change the ceiling height</h2>
      <form id="job-height-form">
        <label class="job-field"><span>Ceiling height in metres</span><input name="height" type="number" min="1.8" max="15" step="0.01" required value="${height?.ceiling_height_mm != null ? fmt(height.ceiling_height_mm / 1000, 2) : ""}"></label>
        <button class="job-button" type="submit">Save and update Phase 1</button>
        <p class="job-note" role="status" id="job-editor-message"></p>
      </form>
    </section>`;
  }
  if (editor.envelope) {
    const envelope = status.envelope?.[room.room_id] || {};
    const trace = envelope.traces?.[0] || {};
    const edges = envelope.edges || [];
    const edgeFields = edges
      .map(
        (
          edge,
          index,
        ) => `<label class="job-field"><span>Wall ${index + 1}</span>
        <select name="edge-${edge.index}">
          ${[
            ["unknown", "Not resolved"],
            ["external", "Outside"],
            ["mall", "Air-conditioned mall"],
            ["adjacent_tenancy", "Neighbouring tenancy"],
            ["internal", "Conditioned room"],
            ["unconditioned", "Unconditioned space"],
          ]
            .map(
              ([value, label]) =>
                `<option value="${value}" ${edge.boundary === value ? "selected" : ""}>${label}</option>`,
            )
            .join("")}
        </select>
      </label>`,
      )
      .join("");
    return `<section class="job-panel job-room-detail" aria-labelledby="room-detail-title">
      <button class="job-link-button" data-back-room>Back to the room</button>
      <h2 id="room-detail-title">Change the walls or roof</h2>
      ${
        edges.length
          ? `<form id="job-envelope-form" data-trace="${escapeHtml(trace.trace_id || edges[0]?.trace_id || "")}">
          <fieldset><legend>Wall boundaries</legend>${edgeFields}</fieldset>
          <label class="job-field"><span>Above this room</span><select name="roof">
            <option value="unknown" ${envelope.roof === "unknown" ? "selected" : ""}>Not resolved</option>
            <option value="not_exposed" ${envelope.roof === "not_exposed" ? "selected" : ""}>Floor or conditioned space above</option>
            <option value="exposed" ${envelope.roof === "exposed" ? "selected" : ""}>Exposed roof above</option>
          </select></label>
          <button class="job-button" type="submit" ${trace.trace_id ? "" : "disabled"}>Save and update Phase 1</button>
          <p class="job-note" role="status" id="job-editor-message"></p>
        </form>`
          : `<p class="job-muted">No wall outline is available to edit for this room.</p>`
      }
    </section>`;
  }

  const areaLabel = room.area_label || "Area source not recorded";
  const areaValue =
    room.area_m2 == null ? "Not available yet" : `${fmt(room.area_m2)} m²`;
  const heightValue =
    height?.ceiling_height_mm == null
      ? "Not available yet"
      : `${fmt(height.ceiling_height_mm / 1000, 2)} m`;
  const areaNote =
    room.area_label ||
    (room.area_m2 == null
      ? "The area is still being worked out."
      : "Area source not recorded.");
  const surfaceTable = groups.length
    ? `<div class="job-surface-table"><table>
        <thead><tr><th scope="col">Surface</th><th scope="col">m²</th><th scope="col">U</th><th scope="col">Beyond it</th><th scope="col">kW</th></tr></thead>
        <tbody>${surfaceRows(groups)}</tbody>
      </table></div>`
    : `<p class="job-muted">Surface details will appear as the room boundaries are worked out.</p>`;
  const wallList = walls.length
    ? `<button class="job-link-button" data-wall-list>${editor.showWalls ? "Hide each wall" : "See each wall on the plan"}</button>
      ${
        editor.showWalls
          ? `<ol class="job-wall-list">${walls
              .map(
                (wall) =>
                  `<li><b>${escapeHtml(wall.label || "Wall")}</b><span>${fmt(wall.area_m2)} m²; U ${fmt(wall.u_value_w_m2k, 2)} W/m²K; ${fmt(wall.total_kw)} kW</span></li>`,
              )
              .join("")}</ol>`
          : ""
      }`
    : "";
  const assumptionList = assumptions.length
    ? `<ul class="job-assumptions">${assumptions
        .map(
          (item) =>
            `<li>${escapeHtml(item.reason || item.component || "Assumption recorded")} <button class="job-link-button" data-edit-envelope>Change</button></li>`,
        )
        .join("")}</ul>`
    : `<p class="job-muted">No Phase 1 assumptions are recorded for this room.</p>`;
  const useEditor = room.state === "needs_use" ? useOptions(uses, room) : "";
  return `<section class="job-panel job-room-detail" aria-labelledby="room-detail-title">
    <h2 class="job-room-title" id="room-detail-title">${escapeHtml(room.name)}</h2>
    <p class="job-room-use">${escapeHtml(room.kind === "refrigeration" ? "Refrigeration" : "Comfort-HVAC room")}</p>
    ${useEditor}
    <dl class="job-key-facts">
      <div><dt>Floor area</dt><dd class="${areaClass(room)}" title="${escapeHtml(areaLabel)}">${escapeHtml(areaValue)}</dd></div>
      <div><dt>Ceiling height</dt><dd class="${height?.origin === "preliminary_fallback" ? "assumed" : ""}" title="${escapeHtml(height?.origin || "Height source not recorded")}">${escapeHtml(heightValue)} <button class="job-link-button" data-edit-height>Change</button></dd></div>
      <div><dt>Empty-room load</dt><dd>${room.phase1_kw == null ? "Not calculated yet" : `${fmt(room.phase1_kw)} kW`}</dd></div>
    </dl>
    <p class="job-caption">Area: ${escapeHtml(areaNote)} Height: ${escapeHtml(height?.origin === "preliminary_fallback" ? "Typical 2.7 m height" : height?.origin ? "Matched to drawing evidence" : "Height not available yet")}.</p>
    <h3>What the load is worked out from</h3>
    ${surfaceTable}${wallList}
    <h3>What the AI assumed</h3>
    ${assumptionList}
    <div class="job-editor-actions">
      <button class="job-button secondary" data-edit-area>Correct the area</button>
      <button class="job-button secondary" data-edit-envelope>Change a wall or the roof</button>
    </div>
  </section>`;
}

function needsYou(plan, projectId) {
  const rows = (plan.rooms || []).filter((room) =>
    ["needs_area", "needs_use"].includes(room.state),
  );
  if (!rows.length) return "";
  const items = rows
    .map((room) => {
      if (room.state === "needs_area") {
        return `<li><form class="job-area-need" data-room-id="${escapeHtml(room.room_id)}">
          <label for="area-${escapeHtml(room.room_id)}">Type the area of ${escapeHtml(room.name)}</label>
          <div class="job-inline-edit"><input id="area-${escapeHtml(room.room_id)}" name="area" type="number" min="0.1" step="0.1" inputmode="decimal" required aria-label="Area of ${escapeHtml(room.name)} in square metres"><span>m²</span><button class="job-button secondary" type="submit">Save area</button></div>
          <p class="job-note" role="status"></p>
        </form></li>`;
      }
      return `<li><a href="#/job/${encodeURIComponent(projectId)}/rooms/${encodeURIComponent(room.room_id)}">Say what the ${escapeHtml(room.name)} is used for</a></li>`;
    })
    .join("");
  return `<section class="job-needs" aria-labelledby="needs-title">
    <h2 id="needs-title">${rows.length} ${rows.length === 1 ? "thing needs" : "things need"} you</h2>
    <ul>${items}</ul>
  </section>`;
}

function renderLayout(plan, status, roomId, uses, projectId, editor = {}) {
  const rooms = plan.rooms || [];
  const room =
    rooms.find((item) => item.room_id === roomId) ||
    rooms.find(
      (item) => item.kind !== "refrigeration" && item.state !== "left_out",
    ) ||
    rooms[0];
  if (!room) {
    return {
      room: null,
      content: `<section class="job-panel"><h2>Rooms will appear as the drawing set is read</h2></section>`,
    };
  }
  const level = room.level || "Floor plan";
  const pages = room.source_pages?.length
    ? `Drawing page${room.source_pages.length === 1 ? "" : "s"} ${room.source_pages.join(", ")}`
    : "";
  const left = `<section class="job-panel job-room-plan" aria-labelledby="floor-title">
    <div class="job-section-title"><h2 id="floor-title">${escapeHtml(level)}</h2>${pages ? `<span>${escapeHtml(pages)}</span>` : ""}</div>
    ${planSvg(plan, { selectedRoom: room.room_id, onSelect: (id) => navigate(projectId, "rooms", id) })}
    ${lineLegend()}
    <h3>Rooms on this floor</h3>
    <div class="job-table-wrap"><table class="job-room-table">
      <thead><tr><th scope="col">Room</th><th scope="col">Area</th><th scope="col">Empty-room load</th><th scope="col">Phase 1</th></tr></thead>
      <tbody>${roomRows(plan, room)}</tbody>
    </table></div>
  </section>`;
  const remaining = plan.phase1_report?.supervisor_exceptions || [];
  const exceptionPanel = remaining.length
    ? `<section class="job-panel" data-model-exceptions><h2>Unresolved drawing checks</h2><p>The empty-room result covers the supported inputs. These checks remain unresolved.</p><ul>${remaining.map((row) => `<li>${escapeHtml(row.message)}</li>`).join("")}</ul></section>` : "";
  const right = roomDetails(plan, room, status, uses, editor);
  return {
    room,
    content: `${needsYou(plan, projectId)}${exceptionPanel}<div class="job-columns job-rooms-layout">${left}${right}</div>`,
  };
}

async function recalculate(projectId) {
  let job = await sendJson("/api/job-calculation", {
    project_id: projectId,
    reviewer: "Job workspace",
    phase: "phase1_base_room",
  });
  const started = Date.now();
  while (["running", "queued"].includes(job.status)) {
    if (Date.now() - started > 600000) {
      throw new Error(
        "Phase 1 is still running. Return to the job to check its progress.",
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 2000));
    job = await getJson(`/api/job-calculation${query(projectId)}`);
  }
  if (job.status !== "done")
    throw new Error(job.error || "The Phase 1 calculation did not finish.");
}

export async function renderRooms(project, projectId, roomId = "") {
  const plan = await getJson(`/api/job-plan${query(projectId)}`);
  const status = plan.status || {};
  const needsUse = (plan.rooms || []).some(
    (room) => room.state === "needs_use",
  );
  const model = needsUse
    ? await getJson(
        `/api/ai-preliminary-model${query(projectId)}&view=workspace`,
      )
    : {};
  const uses = Object.entries(model.room_scope?.uses || {}).filter(
    ([id]) => id !== "not_a_room",
  );
  const layout = renderLayout(plan, status, roomId, uses, projectId);
  const areaCount = (plan.rooms || []).filter(
    (room) => room.state === "finding_area",
  ).length;
  const needsCount = (plan.rooms || []).filter((room) =>
    ["needs_area", "needs_use"].includes(room.state),
  ).length;
  const surfaceCount = (plan.rooms || []).filter(
    (room) => room.state === "finding_surfaces",
  ).length;
  let statusLine =
    "The AI is working through each room’s walls, roof and floor.";
  if (status.phase1_complete)
    statusLine = "Phase 1 is complete for the rooms with resolved evidence.";
  else if (areaCount)
    statusLine = `The AI is working out the area of ${areaCount} ${areaCount === 1 ? "room" : "rooms"}.`;
  else if (needsCount)
    statusLine = `${needsCount} ${needsCount === 1 ? "room needs" : "rooms need"} an area or a use before Phase 1 can finish.`;
  else if (surfaceCount)
    statusLine = `The AI is working out the boundaries of ${surfaceCount} ${surfaceCount === 1 ? "room" : "rooms"}.`;
  return {
    content: layout.content,
    status,
    statusLine,
    bind: () => bindRoomActions(projectId, layout.room, plan, status, uses),
  };
}

function bindRoomActions(projectId, room, plan, status, uses) {
  document.querySelectorAll("[data-room]").forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "1";
    button.addEventListener("click", () =>
      navigate(projectId, "rooms", button.dataset.room),
    );
  });

  const rerender = (editor = {}) => {
    const { content } = renderLayout(
      plan,
      status,
      room.room_id,
      uses,
      projectId,
      editor,
    );
    const columns = document.querySelector(".job-main .job-columns");
    const needs = document.querySelector(".job-main .job-needs");
    if (columns)
      columns.outerHTML = content.replace(
        /^<section class="job-needs"[\s\S]*?<\/section>/,
        "",
      );
    else
      document
        .querySelector(".job-main")
        ?.insertAdjacentHTML("beforeend", content);
    needs?.remove();
    bindRoomActions(projectId, room, plan, status, uses);
  };

  document.querySelectorAll("[data-edit-area]").forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "1";
    button.addEventListener("click", () => {
      const input = document.querySelector(`#area-${CSS.escape(room.room_id)}`);
      if (input) {
        input.focus();
        return;
      }
      const detail = document.querySelector(".job-room-detail");
      detail?.insertAdjacentHTML(
        "afterbegin",
        `<form class="job-area-need" data-single-area="true"><label for="correct-area">Correct the area of ${escapeHtml(room.name)}</label><div class="job-inline-edit"><input id="correct-area" name="area" type="number" min="0.1" step="0.1" required value="${room.area_m2 ?? ""}"><span>m²</span><button class="job-button secondary" type="submit">Save area</button></div><p class="job-note" role="status"></p></form>`,
      );
      bindRoomActions(projectId, room, plan, status, uses);
    });
  });

  document.querySelectorAll(".job-area-need").forEach((form) => {
    if (form.dataset.bound) return;
    form.dataset.bound = "1";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button");
      const targetRoom =
        plan.rooms.find((item) => item.room_id === form.dataset.roomId) || room;
      const area = Number(form.elements.area.value);
      if (!Number.isFinite(area) || area <= 0) {
        form.elements.area.setCustomValidity(
          "Enter an area greater than zero.",
        );
        form.elements.area.reportValidity();
        return;
      }
      form.elements.area.setCustomValidity("");
      button.disabled = true;
      try {
        await sendJson("/api/room-area-override", {
          project_id: projectId,
          room_id: targetRoom.room_id,
          label: targetRoom.name,
          level_name: targetRoom.level || "",
          area_m2: area,
          edited_by: "Job workspace",
        });
        await recalculate(projectId);
        navigate(projectId, "rooms", targetRoom.room_id);
      } catch (error) {
        form.querySelector(".job-note").textContent =
          `Could not save the area: ${error.message}`;
        button.disabled = false;
      }
    });
  });

  document.querySelectorAll("[data-room-use]").forEach((select) => {
    if (select.dataset.bound) return;
    select.dataset.bound = "1";
    select.addEventListener("change", async () => {
      if (!select.value) return;
      select.disabled = true;
      try {
        await sendJson("/api/room-use-resolution", {
          project_id: projectId,
          action: "apply_override",
          room_id: select.dataset.roomUse,
          taxonomy_id: select.value,
          reviewer: "Job workspace",
          note: "Use chosen in the job workspace.",
        });
        await recalculate(projectId);
        navigate(projectId, "rooms", room.room_id);
      } catch (error) {
        select.disabled = false;
        const note = select
          .closest(".job-room-detail")
          ?.querySelector(".job-note");
        if (note)
          note.textContent = `Could not save the room use: ${error.message}`;
      }
    });
  });

  document.querySelectorAll("[data-edit-envelope]").forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "1";
    button.addEventListener("click", () => rerender({ envelope: true }));
  });
  document
    .querySelector("[data-edit-height]")
    ?.addEventListener("click", () => rerender({ height: true }));
  document
    .querySelector("[data-back-room]")
    ?.addEventListener("click", () => rerender());
  document.querySelector("[data-wall-list]")?.addEventListener("click", () => {
    rerender({ showWalls: !document.querySelector(".job-wall-list") });
  });

  const envelopeForm = document.querySelector("#job-envelope-form");
  envelopeForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const current = status.envelope?.[room.room_id] || {};
    const edges = (current.edges || []).map((edge) => ({
      index: edge.index,
      boundary: form.elements[`edge-${edge.index}`].value,
    }));
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await sendJson("/api/reviewer-room-geometry", {
        action: "classify_envelope",
        project_id: projectId,
        room_id: room.room_id,
        trace_id: form.dataset.trace,
        edges,
        roof: form.elements.roof.value,
        reviewer: "Job workspace",
        confirm_roof: true,
        confirmed_edges: [],
      });
      await recalculate(projectId);
      navigate(projectId, "rooms", room.room_id);
    } catch (error) {
      form.querySelector("#job-editor-message").textContent =
        `Could not save: ${error.message}`;
      button.disabled = false;
    }
  });

  const heightForm = document.querySelector("#job-height-form");
  heightForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = heightForm.querySelector("button[type=submit]");
    const metres = Number(heightForm.elements.height.value);
    if (!Number.isFinite(metres) || metres < 1.8 || metres > 15) {
      heightForm.elements.height.setCustomValidity(
        "Enter a height from 1.8 to 15 metres.",
      );
      heightForm.elements.height.reportValidity();
      return;
    }
    button.disabled = true;
    try {
      await sendJson("/api/room-height-override", {
        project_id: projectId,
        room_key: room.room_id,
        label: room.name,
        level_name: room.level || "",
        ceiling_height_mm: Math.round(metres * 1000),
        edited_by: "Job workspace",
      });
      await recalculate(projectId);
      navigate(projectId, "rooms", room.room_id);
    } catch (error) {
      heightForm.querySelector("#job-editor-message").textContent =
        `Could not save: ${error.message}`;
      button.disabled = false;
    }
  });
}
