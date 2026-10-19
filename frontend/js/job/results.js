import { getJson, sendJson, escapeHtml, fmt } from "./api.js";
import { planSvg } from "./plan.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
const LOAD_PARTS = {
  outside_air: { label: "Fresh air", color: "#163B50" },
  infiltration: { label: "Air leakage", color: "#163B50" },
  people: { label: "People", color: "#246B91" },
  lighting: { label: "Lights", color: "#6FA3C2" },
  equipment_refrigeration: { label: "Equipment", color: "#B9D3E2" },
  envelope: { label: "Walls and roof", color: "#FFFFFF", outline: true },
  glazing_conduction: {
    label: "Window conduction",
    color: "#FFFFFF",
    outline: true,
  },
  glazing_solar: {
    label: "Window solar gain",
    color: "#FFFFFF",
    outline: true,
  },
  fan_heat: { label: "Fan heat", color: "#B9D3E2" },
};

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function roomPeakFor(room, roomPeaks) {
  const name = String(room.name || "")
    .trim()
    .toLowerCase();
  return roomPeaks.find(
    (peak) =>
      String(peak.name || peak.room_name || "")
        .trim()
        .toLowerCase() === name,
  );
}

function componentEntries(components) {
  return Object.entries(components || {})
    .map(([key, raw]) => [
      key,
      typeof raw === "number"
        ? raw
        : Number(raw?.total_kw ?? raw?.kw ?? raw?.value),
    ])
    .filter(([, value]) => Number.isFinite(value) && value > 0);
}

function bar(components, roomLoadValue, maxLoad, historical = false) {
  const entries = componentEntries(components);
  const componentNames = entries.map(
    ([key, value]) => `${LOAD_PARTS[key]?.label || key} ${fmt(value)} kW`,
  );
  const description = componentNames.length
    ? componentNames.join(", ")
    : "No component breakdown available";
  const aria = historical
    ? `Past breakdown, out of date. ${description}`
    : description;
  const pieces = entries
    .map(([key, value]) => {
      const part = LOAD_PARTS[key] || { label: key, color: "#86B3CE" };
      const border = part.outline ? "border:1px solid #163B50;" : "";
      const outdated = historical ? " (out of date)" : "";
      return `<i title="${escapeHtml(part.label)}: ${fmt(value)} kW${outdated}" style="width:${Math.max(0, (value / maxLoad) * 100)}%;background:${part.color};${border}"></i>`;
    })
    .join("");
  const width =
    isNumber(roomLoadValue) && maxLoad > 0
      ? Math.min(100, (roomLoadValue / maxLoad) * 100)
      : 0;
  return `<span class="job-stacked-bar" role="img" aria-label="${escapeHtml(aria)}" style="width:${width}%">${pieces}</span>`;
}

function roomRows(rooms, roomPeaks, maxLoad, stale) {
  return rooms
    .map((room) => {
      const peak = roomPeakFor(room, roomPeaks);
      const total = isNumber(room.final_kw) ? room.final_kw : null;
      const historical = total == null && stale;
      const breakdownTotal = historical
        ? Number(
            peak?.final_design_total_kw ??
              peak?.design_total_kw ??
              peak?.design_kw,
          )
        : total;
      const area = room.area_m2 == null ? "Not available" : fmt(room.area_m2);
      return `<tr>
        <th scope="row">${escapeHtml(room.name)}</th>
        <td>${area}</td>
        <td>${bar(peak?.components, breakdownTotal, maxLoad, historical)}</td>
        <td><b>${total == null ? "Not calculated yet" : `${fmt(total)} kW`}</b></td>
      </tr>`;
    })
    .join("");
}

function legend() {
  return `<ul class="job-component-legend">
    ${Object.values(LOAD_PARTS)
      .filter(
        (part, index, all) =>
          all.findIndex((entry) => entry.label === part.label) === index,
      )
      .map(
        (part) =>
          `<li><i class="${part.outline ? "outline" : ""}" style="--part-color:${part.color}"></i>${escapeHtml(part.label)}</li>`,
      )
      .join("")}
  </ul>`;
}

function assumptionsList(items, projectId) {
  if (!items.length) return `<p>No remaining assumptions were reported.</p>`;
  const seen = new Set();
  const rows = items
    .map((item) => {
      let text =
        typeof item === "string"
          ? item
          : item.text ||
            item.reason ||
            item.label ||
            item.component ||
            "Assumption recorded";
      if (
        /\b\d+\s+finding\(s\) from the drawings are still to review/i.test(text)
      ) {
        text =
          "Some drawing details still need review; they are not included in this number.";
      }
      text = text
        .replace(/\bfindings?\b/gi, "items")
        .replace(/\bskills?\b/gi, "review steps")
        .replace(/needs a supported area source/gi, "needs its area entered");
      const key = text.trim().toLowerCase();
      if (seen.has(key)) return "";
      seen.add(key);
      const step =
        item.tab === "rooms"
          ? "rooms"
          : item.tab === "project"
            ? "details"
            : "drawings";
      return `<li>${escapeHtml(text)} <a href="#/job/${encodeURIComponent(projectId)}/${step}">Review</a></li>`;
    })
    .filter(Boolean);
  return rows.length
    ? `<ul>${rows.join("")}</ul>`
    : `<p>No remaining assumptions were reported.</p>`;
}

function resultsContent({ plan, report, gaps, projectId, stale, canUpdate }) {
  const peak = report.included_scope_peak || {};
  const total = peak.final_design_total_kw ?? peak.design_total_kw;
  const safetyFactor = Number(peak.safety_factor || 1);
  const allowance = Number(
    peak.safety_allowance_kw ??
      (Number(total) * (safetyFactor - 1)) / Math.max(safetyFactor, 1),
  );
  const rooms = (plan.rooms || []).filter(
    (room) => room.kind === "comfort" && room.included,
  );
  const peaks = report.room_peaks || [];
  const historicalLoads = rooms.map((room) => {
    const roomPeak = roomPeakFor(room, peaks);
    return Number(
      roomPeak?.final_design_total_kw ??
        roomPeak?.design_total_kw ??
        roomPeak?.design_kw,
    );
  });
  const maxLoad = Math.max(
    0.001,
    ...rooms.map((room) => (isNumber(room.final_kw) ? room.final_kw : 0)),
    ...historicalLoads.filter(Number.isFinite),
  );
  const outOfDate = stale
    ? `<div class="job-stale-note" role="status"><span>Out of date.</span>${canUpdate ? `<button class="job-button secondary" id="job-update-final">Update the final load</button>` : `<a class="job-button secondary" href="#/job/${encodeURIComponent(projectId)}/rooms">Review room inputs before updating</a>`}<span id="job-update-status"></span></div>`
    : "";
  const reportUrl = gaps.report_url || gaps.html_url || gaps.markdown_url;
  const assumptions = gaps.assumptions || gaps.items || gaps.open_items || [];
  const statusSentence = stale
    ? `The previous result covered ${peaks.length} rooms${peak.display_hour == null ? "" : ` at hour ${escapeHtml(peak.display_hour)}`}. Room-by-room loads are out of date. Safety allowance: ${fmt(allowance)} kW.`
    : `${rooms.length} rooms counted${peak.display_hour == null ? "" : ` at the peak hour ${escapeHtml(peak.display_hour)}`}. Safety allowance: ${fmt(allowance)} kW. Refrigeration is sized separately.`;
  return `${outOfDate}
    <section class="job-panel job-result-summary ${stale ? "stale" : ""}">
      <div><h1>Design cooling load for the tenancy</h1>
        <strong class="job-total">${fmt(total)} <span>kW</span></strong>
        <p>${statusSentence}</p>
        ${stale
          ? `<p class="job-muted">Update the final load before creating the report.</p>`
          : `<div class="job-report" data-report>
              <button class="job-button" type="button" data-create-report>Create the report</button>
              ${reportUrl ? `<a class="job-link-button" href="${escapeHtml(reportUrl)}" download>Download the last report</a>` : ""}
              <p class="job-note" role="status" data-report-status></p>
            </div>`}
      </div>
    </section>
    <div class="job-columns job-results-columns">
      <section class="job-panel ${stale ? "stale" : ""}" aria-label="Load by room on the plan">
        <h2>Load per square metre</h2>
        ${planSvg(plan, { shadeLoads: true })}
        <div class="job-load-scale"><span>Lower load</span><i><b></b><b></b><b></b><b></b><b></b></i><span>Higher load</span></div>
        <p class="job-caption">Darker rooms need more cooling for their size.</p>
      </section>
      <section class="job-panel ${stale ? "stale" : ""}" aria-labelledby="each-room-title">
        <h2 id="each-room-title">Each room</h2>
        ${legend()}
        <div class="job-table-wrap"><table class="job-results-table">
          <thead><tr><th scope="col">Room</th><th scope="col">m²</th><th scope="col">Where the load comes from</th><th scope="col">kW</th></tr></thead>
          <tbody>${roomRows(rooms, peaks, maxLoad, stale)}
            <tr><th colspan="3">Safety allowance, ${Math.round((safetyFactor - 1) * 100)}%</th><td>${fmt(allowance)} kW</td></tr>
            <tr class="job-grand-total"><th colspan="3">Design cooling load</th><td>${fmt(total)} kW</td></tr>
          </tbody>
        </table></div>
      </section>
    </div>
    <section class="job-panel ${stale ? "stale" : ""}"><h2>Still assumed</h2>${assumptionsList(assumptions, projectId)}</section>`;
}

// The cooling report (HTML and PDF) is built on the server from the current final load, then offered to open or save.
function bindReport(projectId) {
  const button = document.querySelector("[data-create-report]");
  const status = document.querySelector("[data-report-status]");
  button?.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Creating the report…";
    try {
      const report = await sendJson("/api/report-package", { project_id: projectId, report_type: "cooling" });
      status.innerHTML = `Report ready: <a href="${escapeHtml(report.pdf_url)}" download>Download the PDF</a> or <a href="${escapeHtml(report.html_url)}" target="_blank" rel="noopener">open it in a new tab</a>.`;
    } catch (error) {
      status.textContent = `The report couldn’t be created: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

export async function renderResults(projectId) {
  const [plan, gaps] = await Promise.all([
    getJson(`/api/job-plan${query(projectId)}`),
    getJson(`/api/result-status${query(projectId)}`).catch(() => ({})),
  ]);
  const status = plan.status || {};
  const report = plan.final_report || {};
  const total =
    report.included_scope_peak?.final_design_total_kw ??
    report.included_scope_peak?.design_total_kw;
  if (!status.results_ready || total == null) {
    return {
      status,
      statusLine: status.result_stale
        ? "The final load is out of date. Update it in Operating loads."
        : "Results will be ready after operating loads are complete.",
      content: `<section class="job-panel"><h1>Results are locked</h1><p>The final load appears here after people, lights, equipment and airflow are included.</p><a class="job-button" href="#/job/${encodeURIComponent(projectId)}/operating">Continue to operating loads</a></section>`,
    };
  }
  const content = resultsContent({
    plan,
    report,
    gaps,
    projectId,
    stale: Boolean(status.result_stale),
    canUpdate:
      Boolean(status.phase1_complete) &&
      Number(status.needs_you?.phase2 || 0) === 0,
  });
  const statusLine = status.result_stale
    ? "The final load is out of date. Update it before using the result."
    : "The final load is current for the saved drawing and operating inputs.";
  return {
    status,
    statusLine,
    content,
    bind: () => {
      bindReport(projectId);
      document
        .querySelector("#job-update-final")
        ?.addEventListener("click", async (event) => {
          const button = event.currentTarget;
          const line = document.querySelector("#job-update-status");
          button.disabled = true;
          if (line) line.textContent = "Updating…";
          try {
            const current = await getJson(
              `/api/job-calculation${query(projectId)}`,
            );
            if (["queued", "running"].includes(current.status)) {
              throw new Error(
                "A calculation is already running. Wait for it to finish before retrying.",
              );
            }
            let job = await sendJson("/api/job-calculation", {
              project_id: projectId,
              phase: "full",
              reviewer: "Job workspace",
            });
            while (["queued", "running"].includes(job.status)) {
              if (line) line.textContent = "Calculating the final load…";
              await new Promise((resolve) => setTimeout(resolve, 2000));
              job = await getJson(`/api/job-calculation${query(projectId)}`);
            }
            if (job.status !== "done")
              throw new Error(job.error || "The final load did not update.");
            location.reload();
          } catch (error) {
            if (line) line.textContent = error.message;
            button.disabled = false;
          }
        });
    },
  };
}
