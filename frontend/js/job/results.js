import { getJson, sendJson, escapeHtml, fmt, labelBadge, plainNote } from "./api.js";
import { planSvg } from "./plan.js";

const query = (projectId) => `?project_id=${encodeURIComponent(projectId)}`;
const LOAD_PARTS = {
  outside_air: { label: "Fresh air", color: "#3d4650" },
  infiltration: { label: "Air leakage", color: "#3d4650" },
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
  // By the room list's identity first: a name can repeat on another level.
  const byId = roomPeaks.find(
    (peak) => peak.source_room_id && peak.source_room_id === room.room_id,
  );
  if (byId) return byId;
  const name = String(room.name || "")
    .trim()
    .toLowerCase();
  const named = roomPeaks.filter(
    (peak) =>
      String(peak.name || peak.room_name || "")
        .trim()
        .toLowerCase() === name,
  );
  // A name shared by rooms on two levels is never guessed.
  return named.length === 1 || !named.some((peak) => peak.source_room_id)
    ? named[0]
    : undefined;
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
      const border = part.outline ? "border:1px solid #3d4650;" : "";
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
    .map((room, index) => {
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
      const name = escapeHtml(room.display_name || room.name);
      const detailId = `job-room-hours-${index}`;
      return `<tr class="job-room-row">
        <th scope="row"><button type="button" class="job-room-toggle" aria-expanded="false" aria-controls="${detailId}" data-room-toggle><span aria-hidden="true">+</span> ${name}</button></th>
        <td>${area}</td>
        <td>${bar(peak?.components, breakdownTotal, maxLoad, historical)}</td>
        <td><b>${total == null ? "—" : `${fmt(total)} kW`}</b></td>
      </tr>
      <tr class="job-room-detail" id="${detailId}" hidden><td colspan="4">${roomProfile(room, peak, historical)}</td></tr>`;
    })
    .join("");
}

const hourLabel = (hour) => `${String(hour).padStart(2, "0")}:00`;

// Chart colours: the design system's blues, dark to light, the two lightest outlined (DESIGN.md: one primary blue).
const DAY_GROUP_STYLE = {
  air: { color: "#3d4650" },
  people: { color: "#246B91" },
  sun: { color: "#4F8DB3" },
  lights: { color: "#7FAECB" },
  equipment: { color: "#B9D3E2" },
  conduction: { color: "#FFFFFF", outline: true },
  fan: { color: "#DCE8EF", outline: true },
};

function niceTop(value) {
  for (const step of [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]) {
    const ticks = Math.max(1, Math.ceil(value / step - 1e-9));
    if (ticks <= 6) return [ticks * step, step];
  }
  return [value, value / 5];
}

// Stacked hourly columns from zero: positive groups stack up, negative ones (heat leaving at night) down.
function hourlyChartSvg({ hours, labels, peakHour, peakKw, title, compact = false }) {
  const width = compact ? 560 : 720;
  const height = compact ? 190 : 300;
  const [left, right, top, bottom] = [44, 10, compact ? 24 : 30, 34];
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const ups = hours.map((row) => Object.values(row.groups || {}).filter((v) => v > 0).reduce((a, b) => a + b, 0));
  const downs = hours.map((row) => Object.values(row.groups || {}).filter((v) => v < 0).reduce((a, b) => a + b, 0));
  const [yTop, step] = niceTop(Math.max(0.01, ...ups, ...hours.map((row) => row.total)) * 1.08);
  const lowest = Math.min(0, ...downs, ...hours.map((row) => row.total));
  // Below zero in whole steps of the same grid, so the gridlines fall on round numbers through zero.
  const yBottomAbs = lowest < 0 ? Math.ceil(-lowest / step - 1e-9) * step : 0;
  const span = yTop + yBottomAbs;
  const y = (kw) => top + ((yTop - kw) / span) * plotH;
  const barW = plotW / 24;
  const parts = [`<svg class="job-hour-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(title)}">`];
  for (let tick = -yBottomAbs; tick <= yTop + 1e-9; tick += step) {
    const value = Math.round(tick * 1000) / 1000;
    parts.push(`<line x1="${left}" x2="${width - right}" y1="${y(value).toFixed(1)}" y2="${y(value).toFixed(1)}" class="${value === 0 ? "zero" : "grid"}"/><text x="${left - 6}" y="${(y(value) + 4).toFixed(1)}" class="axis" text-anchor="end">${fmt(value)}</text>`);
  }
  parts.push(`<text x="12" y="${top + plotH / 2}" class="axis" text-anchor="middle" transform="rotate(-90 12 ${top + plotH / 2})">kW</text>`);
  hours.forEach((row, hour) => {
    const x = left + hour * barW + 1.5;
    let up = 0;
    let down = 0;
    for (const { key, label } of labels) {
      const value = Number(row.groups?.[key] || 0);
      if (!value) continue;
      const style = DAY_GROUP_STYLE[key] || { color: "#86B3CE" };
      const from = value > 0 ? up : down;
      const to = from + value;
      if (value > 0) up = to;
      else down = to;
      const yA = y(Math.max(from, to));
      const h = Math.max(Math.abs(y(from) - y(to)) - 0.5, 0.5);
      parts.push(`<rect x="${x.toFixed(1)}" y="${yA.toFixed(1)}" width="${(barW - 3).toFixed(1)}" height="${h.toFixed(1)}" fill="${style.color}"${style.outline ? ' stroke="#3d4650" stroke-width="0.75"' : ""}><title>${hourLabel(hour)} ${escapeHtml(label)}: ${fmt(value)} kW</title></rect>`);
    }
    if (!labels.length || !Object.keys(row.groups || {}).length) {
      const yA = y(Math.max(0, row.total));
      parts.push(`<rect x="${x.toFixed(1)}" y="${yA.toFixed(1)}" width="${(barW - 3).toFixed(1)}" height="${Math.max(Math.abs(y(0) - y(row.total)), 0.5).toFixed(1)}" fill="${hour === peakHour ? "#3d4650" : "#86B3CE"}"><title>${hourLabel(hour)}: ${fmt(row.total)} kW</title></rect>`);
    }
    if (hour % 3 === 0) {
      parts.push(`<text x="${(x + (barW - 3) / 2).toFixed(1)}" y="${height - bottom + 15}" class="axis" text-anchor="middle">${String(hour).padStart(2, "0")}</text>`);
    }
  });
  if (Number.isInteger(peakHour) && peakHour >= 0 && peakHour < 24 && isNumber(peakKw)) {
    const x = left + peakHour * barW + barW / 2;
    const labelY = top - 10;
    const anchor = peakHour > 18 ? "end" : peakHour < 5 ? "start" : "middle";
    parts.push(`<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${labelY + 3}" y2="${(y(Math.max(0, hours[peakHour].total)) - 2).toFixed(1)}" class="peak-line"/><text x="${x.toFixed(1)}" y="${labelY}" class="peak-label" text-anchor="${anchor}" data-peak-label>Peak ${fmt(peakKw)} kW at ${hourLabel(peakHour)}</text>`);
  }
  parts.push(`<text x="${left + plotW / 2}" y="${height - 4}" class="axis" text-anchor="middle">Hour of the day (local clock time)</text></svg>`);
  return parts.join("");
}

function profileHours(profile) {
  const totals = profile?.total_kw;
  if (!Array.isArray(totals) || totals.length !== 24 || !totals.every(isNumber)) return null;
  return totals.map((total, hour) => ({ total, groups: (profile.groups || [])[hour] || {} }));
}

function roomProfile(room, peak, historical) {
  const name = room.display_name || room.name;
  const hours = profileHours(peak?.hourly);
  if (!peak || !hours) {
    return `<p class="job-muted">Hour-by-hour profile not calculated: ${peak ? "the saved result has no 24-hour record for this room" : "this room has no load in the saved result"}.</p>`;
  }
  const peakHour = Number.isInteger(peak.hour) ? peak.hour : hours.reduce((best, row, hour) => (row.total > hours[best].total ? hour : best), 0);
  const peakKw = hours[peakHour].total;
  const own = peak.own_peak;
  const ownLine =
    own && isNumber(own.kw) && (own.scenario_id || own.month)
      ? `<p class="job-caption">This room's own peak over every design day: ${fmt(own.kw)} kW in ${escapeHtml(own.month || "not recorded")} at ${Number.isInteger(own.hour) ? hourLabel(own.hour) : "an unrecorded hour"}.</p>`
      : "";
  return `<div class="job-room-profile" data-room-profile>
      <p><b>${escapeHtml(name)}</b> on the design day: peak ${fmt(peakKw)} kW at ${hourLabel(peakHour)}${historical ? " (out of date)" : ""}.</p>
      ${hourlyChartSvg({ hours, labels: [], peakHour, peakKw, title: `${name}: cooling load by hour on the design day`, compact: true })}
      ${ownLine}
    </div>`;
}

function designDaySection(day, stale) {
  const hours = profileHours(day);
  if (!day || !hours) {
    return `<section class="job-panel ${stale ? "stale" : ""}" aria-labelledby="design-day-title"><h2 id="design-day-title">The design day, hour by hour</h2><p>Not calculated: the saved result has no 24-hour record for the design day.</p></section>`;
  }
  const labels = (day.group_labels || []).filter((group) => hours.some((row) => Number(row.groups?.[group.key] || 0) !== 0));
  const peakHour = Number.isInteger(day.peak_hour) ? day.peak_hour : null;
  const peakKw = isNumber(day.peak_kw) ? day.peak_kw : peakHour == null ? null : hours[peakHour].total;
  const tableRows = hours
    .map((row, hour) => `<tr><th scope="row">${hourLabel(hour)}</th>${labels.map((group) => `<td>${fmt(Number(row.groups?.[group.key] || 0))}</td>`).join("")}<td>${fmt(row.total)}</td></tr>`)
    .join("");
  return `<section class="job-panel ${stale ? "stale" : ""}" aria-labelledby="design-day-title" data-design-day>
      <h2 id="design-day-title">The design day, hour by hour</h2>
      <p>${escapeHtml(day.month || "Design")} design day, by source.${peakHour == null ? "" : ` Peak ${fmt(peakKw)} kW at ${hourLabel(peakHour)}.`}</p>
      ${hourlyChartSvg({ hours, labels, peakHour, peakKw, title: `Cooling load by hour on the ${day.month || "design"} design day, stacked by load component` })}
      <ul class="job-component-legend">${labels
        .map((group) => {
          const style = DAY_GROUP_STYLE[group.key] || { color: "#86B3CE" };
          return `<li><i class="${style.outline ? "outline" : ""}" style="--part-color:${style.color}"></i>${escapeHtml(group.label)}</li>`;
        })
        .join("")}</ul>
      <details class="job-room-why"><summary>Reading the chart</summary>Below zero is heat leaving the rooms (cool hours through walls, roof and windows). Fan heat is the difference between the saved hour total and its parts.</details>
      <details class="job-more"><summary>Show the hours as a table</summary>
        <div class="job-table-wrap"><table class="job-results-table job-hour-table">
          <thead><tr><th scope="col">Hour</th>${labels.map((group) => `<th scope="col">${escapeHtml(group.label)}</th>`).join("")}<th scope="col">Total kW</th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table></div>
      </details>
    </section>`;
}

const kwOrNot = (value) => (isNumber(value) ? `${fmt(value)} kW` : "Not calculated");
const setpointLabel = (value) => `${fmt(value)} °C`;

function comparisonContent(comparison, planRooms) {
  if (!comparison || comparison.status !== "calculated") {
    return `<p data-setpoint-state="not_calculated"><b>Not calculated.</b> ${escapeHtml(comparison?.reason || "No comparison is recorded for this result.")}</p>`;
  }
  const variants = comparison.variants || [];
  const rows = variants
    .map((row) => {
      const label = `${setpointLabel(row.setpoint_c)}${row.baseline ? " (as designed)" : ""}`;
      if (row.status === "not_calculated") {
        return `<tr><th scope="row">${label}</th><td colspan="6">Not calculated: ${escapeHtml(row.reason || "no peak recorded")}</td></tr>`;
      }
      const change = row.baseline
        ? "<td>—</td><td>—</td>"
        : `<td>${isNumber(row.delta_kw) ? `${row.delta_kw > 0 ? "+" : ""}${fmt(row.delta_kw)} kW` : "Not calculated"}</td><td>${isNumber(row.delta_pct) ? `${row.delta_pct > 0 ? "+" : ""}${fmt(row.delta_pct)}%` : "Not calculated"}</td>`;
      const when = `${escapeHtml(row.month || "Month not recorded")} ${Number.isInteger(row.hour) ? hourLabel(row.hour) : ""}`.trim();
      return `<tr data-setpoint="${escapeHtml(row.setpoint_c)}"><th scope="row">${label}</th><td><b>${kwOrNot(row.total_kw)}</b></td><td>${kwOrNot(row.sensible_kw)}</td><td>${kwOrNot(row.latent_kw)}</td><td>${when}</td>${change}</tr>`;
    })
    .join("");
  const shown = (row) => {
    const match =
      planRooms.find((room) => row.source_room_id && room.room_id === row.source_room_id) ||
      planRooms.filter((room) => String(room.name || "").trim().toLowerCase() === String(row.name || "").trim().toLowerCase()).at(0);
    return match?.display_name || match?.name || row.name;
  };
  const roomRowsHtml = (comparison.rooms || [])
    .map(
      (room) =>
        `<tr><th scope="row">${escapeHtml(shown(room))}</th>${variants
          .map((variant) => {
            const peak = room.peaks?.[String(variant.setpoint_c)];
            return isNumber(peak?.kw)
              ? `<td>${fmt(peak.kw)} kW<span class="job-caption"> ${escapeHtml(peak.month || "")} ${Number.isInteger(peak.hour) ? hourLabel(peak.hour) : ""}</span></td>`
              : "<td>Not calculated</td>";
          })
          .join("")}</tr>`,
    )
    .join("");
  const rh = isNumber(comparison.relative_humidity) ? ` at ${fmt(comparison.relative_humidity * 100)}% relative humidity` : "";
  return `<p>Only the indoor temperature changes${rh}, kept the same at every setpoint. Walls, floors and ceilings to other cooled rooms move with the room's temperature; everything else is as designed. Peaks can fall in a different month or hour.</p>
    <div class="job-table-wrap"><table class="job-results-table" data-setpoint-table>
      <thead><tr><th scope="col">Indoor setpoint</th><th scope="col">Total</th><th scope="col">Sensible</th><th scope="col">Latent</th><th scope="col">Peak</th><th scope="col">Change</th><th scope="col">Change %</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>
    ${roomRowsHtml ? `<details class="job-more" data-setpoint-rooms><summary>Each room's peak at each setpoint</summary>
      <div class="job-table-wrap"><table class="job-results-table">
        <thead><tr><th scope="col">Room</th>${variants.map((variant) => `<th scope="col">${setpointLabel(variant.setpoint_c)}</th>`).join("")}</tr></thead>
        <tbody>${roomRowsHtml}</tbody>
      </table></div>
      <p class="job-caption">Each room's own largest load over the design days, and when it happens.</p>
    </details>` : ""}`;
}


function heatingContent(heating) {
  const gate = heating?.gate || {};
  if (!heating || heating.status !== "current" || !isNumber(heating.total_kw)) {
    const reasons = (heating?.stale_reasons || []).length
      ? `<ul>${heating.stale_reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>`
      : "";
    return `<div data-heating-state="${escapeHtml(heating?.status || "not_calculated")}">
      <p><b>Not calculated.</b> ${escapeHtml(heating?.reason || "No heating report is recorded for this job.")}</p>${reasons}
      <p><b>Next step:</b> ${escapeHtml(heating?.next_step || "Calculate the heating report for this job.")}</p>
      ${gate.message ? `<p class="job-caption">Heating method: ${escapeHtml(gate.message)}</p>` : ""}
    </div>`;
  }
  const rooms = heating.rooms || [];
  const keys = [...new Set(rooms.flatMap((room) => Object.keys(room.components || {})))];
  const draft = heating.draft
    ? `<p class="job-stale-note" role="status" data-heating-draft><span>Draft.</span> ${escapeHtml(gate.approved ? `The heating report itself is ${heating.report_status || "draft"}.` : gate.message || "The heating method hasn't been approved by an engineer.")} ${escapeHtml(heating.next_step || "")}</p>`
    : "";
  const temperature = (value) => (isNumber(value) ? `${fmt(value)} °C` : "Not recorded");
  return `<div data-heating-state="current">
    ${draft}
    <strong class="job-total job-total-small" data-heating-total>${fmt(heating.total_kw)} <span>kW</span></strong>
    <p>Design heating load for the house${heating.peak?.hour == null ? "" : ` at ${hourLabel(heating.peak.hour)}`}${heating.peak?.title ? ` on the ${escapeHtml(heating.peak.title)} day` : ""}.</p>
    <p>Winter design outdoor temperature: <b>${temperature(heating.outdoor_design_c)}</b>. Indoor: <b>${temperature(heating.indoor_design_c)}</b>.${heating.temperature_note ? ` ${escapeHtml(heating.temperature_note)}` : ""}</p>
    <div class="job-table-wrap"><table class="job-results-table" data-heating-rooms>
      <thead><tr><th scope="col">Room</th>${keys.map((key) => `<th scope="col">${escapeHtml(key)}</th>`).join("")}<th scope="col">Peak kW</th></tr></thead>
      <tbody>${rooms
        .map((room) => `<tr><th scope="row">${escapeHtml(room.name)}</th>${keys.map((key) => `<td>${isNumber(room.components?.[key]) ? fmt(room.components[key]) : "Not recorded"}</td>`).join("")}<td><b>${kwOrNot(room.peak_kw)}</b></td></tr>`)
        .join("")}
        <tr class="job-grand-total"><th colspan="${keys.length + 1}">Design heating load</th><td>${fmt(heating.total_kw)} kW</td></tr>
      </tbody>
    </table></div>
    ${(heating.blocked_rooms || []).length ? `<p class="job-caption">Not calculated for: ${heating.blocked_rooms.map((row) => escapeHtml(typeof row === "string" ? row : `${row.name || row.room_id}${row.reason ? ` (${row.reason})` : ""}`)).join("; ")}.</p>` : ""}
  </div>`;
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
      text = plainNote(text)
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


// The assumptions behind the number, folded like the Rooms tab's: what needs doing is in the one to-do list at the
// top (2026-11-28 compact pass).
function assumptionsPanel(items, projectId, stale) {
  const list = assumptionsList(items, projectId);
  const count = (list.match(/<li>/g) || []).length;
  if (!count) return `<section class="job-panel ${stale ? "stale" : ""}" data-still-assumed><h2>Still assumed</h2>${list}</section>`;
  return `<section class="job-panel job-collapsible-panel ${stale ? "stale" : ""}" data-still-assumed><details><summary><h2>${count} ${count === 1 ? "assumption" : "assumptions"} in this result</h2></summary>${list}</details></section>`;
}

const YEAR_SHADES = ["#f4f8fa", "#e3eef4", "#c9dde9", "#a9c9dc", "#86b3ce", "#5f93b6"];

// The load through the year (user decision 2026-10-25): the 21st of every month, all 24 hours, each with its own sun.
function yearSection(seasonal, stale) {
  const months = (seasonal?.months || []).filter(
    (row) => Array.isArray(row.hourly_kw) && row.hourly_kw.length === 24,
  );
  if (!months.length) return "";
  const top = Math.max(0.001, ...months.flatMap((row) => row.hourly_kw));
  const lead = seasonal.monthly
    ? `The 21st of each month, hour by hour. The largest, ${fmt(seasonal.design_kw)} kW in ${escapeHtml(seasonal.design_month)} at ${hourLabel(seasonal.design_hour)}, sizes the system.`
    : `Only the summer design day is calculated: no monthly temperatures are available for this location, so the other months aren't shown.`;
  const head = Array.from(
    { length: 24 },
    (_value, hour) =>
      `<th scope="col" class="job-year-hour">${hour % 3 === 0 ? String(hour).padStart(2, "0") : `<span class="job-sr-only">${String(hour).padStart(2, "0")}:00</span>`}</th>`,
  ).join("");
  const rows = months
    .map((row) => {
      const cells = row.hourly_kw
        .map((kw, hour) => {
          const shade =
            YEAR_SHADES[Math.min(5, Math.max(0, Math.round((kw / top) * 5)))];
          const peak = hour === row.peak_hour ? " peak" : "";
          return `<td class="job-year-cell${peak}" style="background:${shade}" title="${escapeHtml(row.month)} ${hourLabel(hour)}: ${fmt(kw)} kW"><span class="job-sr-only">${fmt(kw)} kW</span></td>`;
        })
        .join("");
      const design = row.month === seasonal.design_month ? " job-year-design" : "";
      return `<tr class="${design.trim()}"><th scope="row">${escapeHtml(row.month)}</th>${cells}<td class="job-year-peak">${fmt(row.peak_kw)} kW at ${hourLabel(row.peak_hour)}</td></tr>`;
    })
    .join("");
  return `<section class="job-panel ${stale ? "stale" : ""}" aria-labelledby="year-title">
      <h2 id="year-title">Load through the year</h2>
      <p>${lead}</p>
      <div class="job-table-wrap"><table class="job-year-grid">
        <thead><tr><th scope="col">Month</th>${head}<th scope="col">Peak</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="job-load-scale"><span>Lower load</span><i><b></b><b></b><b></b><b></b><b></b></i><span>Higher load</span></div>
      <details class="job-room-why"><summary>Reading the chart</summary>Each month's 21st is calculated with that day's sun angle and design temperature. Hours are local clock time; the outlined cell is that month's peak.</details>
    </section>`;
}

function resultsContent({ plan, report, gaps, resultLabel, projectId, stale, canUpdate }) {
  const peak = report.included_scope_peak || {};
  const total = peak.final_design_total_kw ?? peak.design_total_kw;
  const safetyFactor = Number(peak.safety_factor || 1);
  const allowance = Number(
    peak.safety_allowance_kw ??
      (Number(total) * (safetyFactor - 1)) / Math.max(safetyFactor, 1),
  );
  // No allowance is added by default; a project policy can still set one, and only then is it shown.
  const allowanceText =
    allowance > 0 ? ` Safety allowance: ${fmt(allowance)} kW.` : "";
  const allowanceRow =
    allowance > 0
      ? `<tr><th colspan="3">Safety allowance, ${Math.round((safetyFactor - 1) * 100)}%</th><td>${fmt(allowance)} kW</td></tr>`
      : "";
  const household = peak.household_counted_once;
  const householdLine = household
    ? `<details class="job-room-why" data-household><summary>People counted once</summary><p>Household counted once: the rooms hold ${escapeHtml(household.people_in_rooms)} people at ${escapeHtml(peak.display_hour ?? peak.hour)}:00, the house has ${escapeHtml(household.people_counted)} — ${fmt(Number(household.removed_sensible_kw || 0) + Number(household.removed_latent_kw || 0))} kW of people heat not counted twice.</p></details>`
    : "";
  // A joined room (pieces the drawing reader split) is one row: its load is the peak of the pieces' summed hours.
  const joinedRows = (plan.joined_rooms || [])
    .filter((group) => group.cooled !== false)
    .map((group) => ({ room_id: group.id, name: group.name, display_name: group.name, area_m2: group.area_m2,
                       final_kw: group.final_kw, kind: "comfort", included: true, member_ids: group.member_ids || [] }));
  // In the room list's order, each joined room where its first piece was.
  const placed = new Set();
  const rooms = (plan.rooms || []).flatMap((room) => {
    if (!room.join_id) return room.kind === "comfort" && room.included ? [room] : [];
    const group = joinedRows.find((row) => row.room_id === room.join_id);
    if (!group || placed.has(group.room_id)) return [];
    placed.add(group.room_id);
    return [group];
  });
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
    ? `<div class="job-stale-note job-stale-action" role="status"><span class="job-sr-only">Out of date.</span>${canUpdate ? `<button class="job-button secondary" id="job-update-final">Update the final load</button>` : `<a class="job-button secondary" href="#/job/${encodeURIComponent(projectId)}/rooms">Review room inputs before updating</a>`}<span id="job-update-status"></span></div>`
    : "";
  const reportUrl = gaps.report_url || gaps.html_url || gaps.markdown_url;
  const assumptions = gaps.assumptions || gaps.items || gaps.open_items || [];
  const statusSentence = stale
    ? `The previous result covered ${peaks.length} rooms${peak.display_hour == null ? "" : ` at hour ${escapeHtml(peak.display_hour)}`}. Room-by-room loads are out of date.${allowanceText}`
    : `<span data-rooms-counted>${rooms.length} rooms counted</span>${peak.display_hour == null ? "" : `, peak at ${escapeHtml(String(peak.display_hour).padStart(2, "0"))}:00`}.${allowanceText} Refrigeration is sized separately.`;
  return `${outOfDate}
    <section class="job-panel job-result-summary ${stale ? "stale" : ""}">
      <div><h1>${resultLabel.status === "design" ? "Design" : "Preliminary"} cooling load for the ${plan.status?.dwelling ? "house" : "tenancy"}</h1>
        <div class="job-result-total-line"><strong class="job-total">${fmt(total)} <span>kW</span></strong>
          ${labelBadge(resultLabel.text ? resultLabel : { status: "preliminary", text: "Preliminary" })}</div>
        ${resultLabel.status === "preliminary" && resultLabel.text ? `<p class="job-label-why" data-label-why>${escapeHtml(resultLabel.text)}. Answer the open questions below to make it a design figure.</p>` : ""}
        <p>${statusSentence}</p>
        ${stale ? "" : `<p class="job-range-line" role="status" data-range-line>Working out how far this could move with what the drawings don’t show…</p>`}
        ${householdLine}
        ${stale
          ? ""
          : `<div class="job-report" data-report>
              <button class="job-button" type="button" data-create-report>Create the report</button>
              <button class="job-button secondary" type="button" data-create-heating-report disabled hidden aria-describedby="job-heating-report-note">Create the heating report</button>
              <a class="job-button secondary" href="#/job/${encodeURIComponent(projectId)}/fix">Fix a room or window</a>
              ${reportUrl ? `<a class="job-link-button" href="${escapeHtml(reportUrl)}" download>Download the last report</a>` : ""}
              <p class="job-note" role="status" data-report-status></p>
              <p class="job-caption" id="job-heating-report-note" data-heating-report-note hidden></p>
            </div>`}
      </div>
    </section>
    <div class="job-columns job-results-columns">
      <section class="job-panel ${stale ? "stale" : ""}" aria-label="Load by room on the plan">
        <h2>Load per square metre</h2>
        ${planSvg(plan, { shadeLoads: true })}
        <div class="job-load-scale"><span>Lower load</span><i><b></b><b></b><b></b><b></b><b></b></i><span>Higher load</span></div>
      </section>
      <section class="job-panel ${stale ? "stale" : ""}" aria-labelledby="each-room-title">
        <h2 id="each-room-title">Each room</h2>
        ${legend()}
        <div class="job-table-wrap"><table class="job-results-table">
          ${roomRows(rooms, peaks, maxLoad, stale).includes("<b>—</b>") ? `<caption class="job-caption">— Not calculated yet</caption>` : ""}
          <thead><tr><th scope="col">Room</th><th scope="col">m²</th><th scope="col">Where the load comes from</th><th scope="col">kW</th></tr></thead>
          <tbody>${roomRows(rooms, peaks, maxLoad, stale)}
            ${allowanceRow}
            <tr class="job-grand-total"><th colspan="3">${resultLabel.status === "design" ? "Design" : "Preliminary"} cooling load</th><td>${fmt(total)} kW</td></tr>
          </tbody>
        </table></div>
      </section>
    </div>
    ${stale ? "" : `<section class="job-panel" aria-labelledby="range-title" data-range-panel>
      <h2 id="range-title">What could change the number</h2>
      <div data-range-body><p class="job-muted" role="status">Loading…</p></div>
    </section>
    <section class="job-panel" aria-labelledby="architect-title" data-architect-panel>
      <h2 id="architect-title">Ask the architect</h2>
      <div data-architect-body><p class="job-muted" role="status">Loading…</p></div>
    </section>`}
    <section class="job-panel ${stale ? "stale" : ""}" aria-labelledby="setpoint-title" data-setpoint-panel>
      <h2 id="setpoint-title">Indoor setpoint comparison</h2>
      <div data-setpoint-body><p class="job-muted" role="status">Loading…</p></div>
    </section>
    ${designDaySection(report.design_day_profile, stale)}
    ${yearSection(report.seasonal, stale)}
    <section class="job-panel" aria-labelledby="heating-title" data-heating-panel>
      <h2 id="heating-title">Design heating load</h2>
      <div data-heating-body><p class="job-muted" role="status">Loading…</p></div>
    </section>
    ${assumptionsPanel(assumptions, projectId, stale)}`;
}

// How far the saved load could move with the facts the drawings don't settle: every kW is a full calculation with one
// input changed (backend/assumption_range_service.py). Nothing is shown that the server didn't calculate.
function rangeLine(range) {
  if (range?.status === "calculated" && range.low_kw != null) {
    const top = range.unknowns.filter((row) => row.swing_kw > 0).slice(0, 2).map((row) => row.label.toLowerCase());
    const unitNote = range.unknowns.some((row) => row.changes_unit) ? " Some answers would change the unit." : "";
    return `Could be <strong data-range-low>${fmt(range.low_kw)}</strong>–<strong data-range-high>${fmt(range.high_kw)}</strong> kW${top.length ? ` depending on the ${escapeHtml(top.join(" and "))}` : ""}.${unitNote}`;
  }
  if (range?.status === "running") return `Working out how far this could move with what the drawings don’t show (${escapeHtml(range.done ?? 0)} of ${escapeHtml(range.total ?? "?")} calculations)…`;
  if (range?.status === "nothing_open") return "The windows, walls, roof and north are answered or read from the drawings.";
  return `Range not calculated: ${escapeHtml(range?.reason || "unknown reason")}`;
}

function rangeContent(range) {
  if (range?.status === "running") {
    return `<p class="job-muted" role="status" data-range-state="running">Each unknown is tried through the full calculation on a copy of the job: ${escapeHtml(range.done ?? 0)} of ${escapeHtml(range.total ?? "?")} done. This takes a few minutes the first time.</p>`;
  }
  if (range?.status !== "calculated") {
    return `<p data-range-state="${escapeHtml(range?.status || "not_calculated")}">${range?.status === "nothing_open" ? "" : "Not calculated. "}${escapeHtml(range?.reason || "")}</p>`;
  }
  const rows = range.unknowns.flatMap((unknown) => {
    const cases = unknown.cases.length ? unknown.cases : [{ status: "not_calculated", label: "", reason: "No case ran." }];
    return cases.map((item, index) => `<tr data-range-case="${escapeHtml(item.case_id || "")}">
      ${index === 0 ? `<th scope="rowgroup" rowspan="${cases.length}">${escapeHtml(unknown.label)}${unknown.answered ? ` <span class="job-answer-badge ok">Answered since</span>` : ""}<span class="job-caption">${escapeHtml(unknown.assumed || "")}</span></th>` : ""}
      <td>${escapeHtml(item.label)}</td>
      ${item.status === "calculated"
        ? `<td>${fmt(item.kw)} kW</td><td>${item.delta_kw > 0 ? "+" : ""}${fmt(item.delta_kw)} kW (${item.delta_pct > 0 ? "+" : ""}${fmt(item.delta_pct)}%)</td><td>${escapeHtml(item.unit)}${item.changes_unit ? ` <span class="job-answer-badge warn" data-changes-unit>Changes the unit</span>` : ""}</td>`
        : `<td colspan="3">Not calculated: ${escapeHtml(item.reason)}</td>`}
    </tr>`);
  }).join("");
  return `<p data-range-state="calculated">As calculated: <strong>${fmt(range.saved_kw)} kW</strong>, ${escapeHtml(range.unit)}. ${escapeHtml(range.basis)}</p>
    <div class="job-table-wrap"><table class="job-results-table job-range-table">
      <thead><tr><th scope="col">Not shown on the drawings</th><th scope="col">If it is</th><th scope="col">Load</th><th scope="col">Change</th><th scope="col">Unit</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

const STATUS_LABEL = { open: "Not asked yet", asked: "Asked – waiting", answered: "Answered" };

function answerForm(item, constructions) {
  const id = escapeHtml(item.id);
  if (item.answer_kind === "glazing") {
    return `<label class="job-field"><span>Glass U-value (W/m²K)</span><input name="u_value_w_m2k" type="number" step="0.1" min="0.5" max="7" required></label>
      <label class="job-field"><span>SHGC</span><input name="shgc" type="number" step="0.01" min="0.05" max="0.95" required></label>
      <label class="job-field"><span>Window type (as the architect described it)</span><input name="value" type="text" maxlength="160"></label>`;
  }
  if (item.answer_kind === "construction") {
    const options = (constructions?.[item.id] || []).map((row) => `<option value="${escapeHtml(row.id)}">${escapeHtml(row.name)} (U ${escapeHtml(row.u_value_w_m2k)})</option>`).join("");
    return `<label class="job-field"><span>Construction (AIRAH handbook table)</span><select name="construction_id"><option value="">Another construction (type its U-value)</option>${options}</select></label>
      <label class="job-field"><span>Or its U-value (W/m²K)</span><input name="u_value_w_m2k" type="number" step="0.01" min="0.1" max="7"></label>
      <label class="job-field"><span>Description</span><input name="value" type="text" maxlength="160"></label>`;
  }
  if (item.answer_kind === "north") {
    return `<fieldset class="job-architect-north"><legend>True north on the plans</legend>
      <label><input type="radio" name="choice" value="as_read" required> As the drawings were read</label>
      <label><input type="radio" name="choice" value="reversed"> The other way round</label>
      <label><input type="radio" name="choice" value="bearing"> The top of the plan faces this bearing:</label>
      <input name="plan_up_azimuth_deg" type="number" step="0.1" min="0" max="359.9" aria-label="Plan-up bearing in degrees for ${id}">
    </fieldset>`;
  }
  return "";
}

function architectContent(list) {
  const items = list?.items || [];
  if (!items.length) {
    return `<p data-architect-state="empty">${list?.range?.status === "running" ? "The list fills in once the range is worked out." : "Nothing to ask: the windows, walls, roof and north are answered or read from the drawings."}</p>`;
  }
  const open = items.filter((item) => item.status !== "answered");
  const mail = list.email || {};
  const mailto = `mailto:?subject=${encodeURIComponent(mail.subject || "")}&body=${encodeURIComponent(mail.body || "")}`;
  return `<p>${open.length ? `${open.length} question${open.length === 1 ? "" : "s"} still open${list.unit_changing_open ? `, ${list.unit_changing_open} of which could change the unit` : ""}. Ranked by how much the answer could move the load.` : "Every question is answered. Update the load to use the answers."}</p>
    ${open.length ? `<div class="job-actions job-architect-actions">
      <a class="job-button" href="${escapeHtml(mailto)}" data-architect-mailto>Email the questions</a>
      <button class="job-button secondary" type="button" data-architect-copy>Copy the email</button>
      <button class="job-button secondary" type="button" data-architect-mark-all>Mark all asked</button>
    </div>` : ""}
    <ol class="job-architect-list">${items.map((item) => `<li class="job-architect-item" data-architect-item="${escapeHtml(item.id)}" data-status="${escapeHtml(item.status)}">
      <div class="job-architect-head"><strong>${escapeHtml(item.label)}</strong>
        <span class="job-answer-badge ${item.status === "answered" ? "ok" : ""}" data-architect-status>${escapeHtml(STATUS_LABEL[item.status] || item.status)}</span>
        ${item.changes_unit ? `<span class="job-answer-badge warn">Could change the unit</span>` : ""}
        ${item.swing_kw != null ? `<span class="job-caption">up to ${fmt(item.swing_kw)} kW</span>` : ""}</div>
      <p>${escapeHtml(item.question)}</p>
      ${item.why ? `<p class="job-caption">${escapeHtml(item.why)}</p>` : ""}
      ${item.status === "answered" ? "" : `<div class="job-row-actions">
        <button class="job-button secondary" type="button" data-architect-toggle="${escapeHtml(item.id)}" data-asked="${item.status === "asked"}">${item.status === "asked" ? "Not asked yet" : "Mark asked"}</button>
        <details class="job-architect-answer"><summary>Type the answer</summary>
          <form data-architect-form="${escapeHtml(item.id)}">${answerForm(item, list.constructions)}
            <button class="job-button" type="submit">Save the answer</button></form>
        </details></div>`}
    </li>`).join("")}</ol>
    <p class="job-note" role="status" data-architect-status-line></p>`;
}

async function loadRangeAndArchitect(projectId) {
  const line = document.querySelector("[data-range-line]");
  const rangeBody = document.querySelector("[data-range-body]");
  const architectBody = document.querySelector("[data-architect-body]");
  if (!rangeBody && !architectBody) return;
  let range;
  for (let attempt = 0; attempt < 240; attempt += 1) {
    range = await getJson(`/api/assumption-range${query(projectId)}`).catch((error) => ({ status: "not_calculated", reason: `The range couldn't be read: ${error.message}` }));
    if (line) line.innerHTML = rangeLine(range);
    if (rangeBody) rangeBody.innerHTML = rangeContent(range);
    if (range.status !== "running" || !document.body.contains(rangeBody)) break;
    await new Promise((resolve) => setTimeout(resolve, 5000));
  }
  await renderArchitect(projectId);
}

// The list after a save is the one the save returned: it already holds the change.
async function renderArchitect(projectId, message = "", saved = null) {
  const body = document.querySelector("[data-architect-body]");
  if (!body) return;
  const list = saved || await getJson(`/api/architect-questions${query(projectId)}`).catch((error) => ({ items: [], error: error.message }));
  body.innerHTML = list.error ? `<p>The list couldn't be read: ${escapeHtml(list.error)}</p>` : architectContent(list);
  const status = body.querySelector("[data-architect-status-line]");
  if (status && message) status.innerHTML = message;
  const save = async (data, done) => {
    try {
      const result = await sendJson("/api/architect-questions", { project_id: projectId, reviewer: "Job workspace", ...data });
      await renderArchitect(projectId, done(result), result);
    } catch (error) {
      if (status) status.textContent = error.message;
    }
  };
  body.querySelector("[data-architect-copy]")?.addEventListener("click", async () => {
    const text = `${list.email.subject}\n\n${list.email.body}`;
    try {
      await navigator.clipboard.writeText(text);
      if (status) status.textContent = "Email copied.";
    } catch {
      if (status) status.textContent = "Couldn't copy: select the questions above and copy them.";
    }
  });
  body.querySelector("[data-architect-mark-all]")?.addEventListener("click", () =>
    save({ action: "mark_asked", ids: list.items.filter((item) => item.status === "open").map((item) => item.id) }, (result) => escapeHtml(result.summary)));
  body.querySelectorAll("[data-architect-toggle]").forEach((button) => button.addEventListener("click", () =>
    save({ action: button.dataset.asked === "true" ? "unmark" : "mark_asked", ids: [button.dataset.architectToggle] }, (result) => escapeHtml(result.summary))));
  body.querySelectorAll("[data-architect-form]").forEach((form) => form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form).entries());
    for (const key of ["u_value_w_m2k", "shgc", "plan_up_azimuth_deg"]) {
      if (data[key] === "" || data[key] == null) delete data[key];
      else data[key] = Number(data[key]);
    }
    save({ action: "answer", id: form.dataset.architectForm, source: "architect", ...data },
      (result) => `Saved: ${escapeHtml(result.summary)} <a href="#/job/${encodeURIComponent(projectId)}/results" onclick="location.reload()">Reload to update the load with it</a>.`);
  }));
}

// The heating report package uses the same report-package call; it is offered only for a current heating report.
function bindHeatingReport(projectId) {
  const button = document.querySelector("[data-create-heating-report]");
  const status = document.querySelector("[data-report-status]");
  button?.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Creating the heating report…";
    try {
      const report = await sendJson("/api/report-package", { project_id: projectId, report_type: "heating" });
      status.innerHTML = `Heating report ready: <a href="${escapeHtml(report.pdf_url)}" download>Download the PDF</a> or <a href="${escapeHtml(report.html_url)}" target="_blank" rel="noopener">open it in a new tab</a>.`;
    } catch (error) {
      status.textContent = `The heating report couldn’t be created: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

// The comparison and the heating result are read after the page shows: the comparison's first read after a new
// result is calculated on the server, and the rest of the results shouldn't wait for it.
async function loadExtraPanels(projectId, planRooms) {
  const setpointBody = document.querySelector("[data-setpoint-body]");
  const heatingBody = document.querySelector("[data-heating-body]");
  const heatingButton = document.querySelector("[data-create-heating-report]");
  const heatingNote = document.querySelector("[data-heating-report-note]");
  const [comparison, heating] = await Promise.all([
    getJson(`/api/setpoint-comparison${query(projectId)}`).catch((error) => ({ status: "not_calculated", reason: `The comparison couldn't be read: ${error.message}` })),
    getJson(`/api/heating-summary${query(projectId)}`).catch((error) => ({ status: "not_calculated", reason: `The heating result couldn't be read: ${error.message}`, next_step: "Reload the page; if it persists, check the server log." })),
  ]);
  if (setpointBody) setpointBody.innerHTML = comparisonContent(comparison, planRooms);
  if (heatingBody) heatingBody.innerHTML = heatingContent(heating);
  if (heatingButton) {
    const ready = heating?.status === "current";
    // Offered only once a heating report exists; the heating panel below says why there isn't one.
    heatingButton.disabled = !ready;
    heatingButton.hidden = !ready;
    if (heatingNote) {
      heatingNote.textContent = ready && heating.draft ? "The heating report will be marked draft." : "";
      heatingNote.hidden = !heatingNote.textContent;
    }
  }
}

function bindRoomToggles() {
  document.querySelectorAll("[data-room-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const detail = document.getElementById(button.getAttribute("aria-controls"));
      const open = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(open));
      button.querySelector("span").textContent = open ? "−" : "+";
      if (detail) detail.hidden = !open;
    });
  });
}

// The cooling report (HTML and PDF) is built on the server from the current final load, then offered to open or save.
function bindReport(projectId) {
  const button = document.querySelector("[data-create-report]");
  const status = document.querySelector("[data-report-status]");
  button?.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Creating the report…";
    try {
      const report = await sendJson("/api/report-package", { project_id: projectId, report_type: "cooling", source: "job" });
      status.innerHTML = `Report ready: <a href="${escapeHtml(report.pdf_url)}" download>Download the PDF</a> or <a href="${escapeHtml(report.html_url)}" target="_blank" rel="noopener">open it in a new tab</a>.`;
    } catch (error) {
      status.textContent = `The report couldn’t be created: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

export async function renderResults(projectId) {
  const [plan, gaps, resultLabel] = await Promise.all([
    getJson(`/api/job-plan${query(projectId)}`),
    getJson(`/api/result-status${query(projectId)}`).catch(() => ({})),
    getJson(`/api/result-label${query(projectId)}`).catch(() => ({ status: "preliminary", text: "Preliminary", reason: "The result label couldn't be read." })),
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
    resultLabel,
    projectId,
    stale: Boolean(status.result_stale),
    canUpdate:
      Boolean(status.phase1_complete) &&
      Number(status.needs_you?.phase2 || 0) === 0,
  });
  // The card above and the out-of-date bar already say whether the load is current (2026-11-28 compact pass).
  const statusLine = "";
  return {
    status,
    statusLine,
    content,
    bind: () => {
      bindReport(projectId);
      bindHeatingReport(projectId);
      bindRoomToggles();
      loadExtraPanels(projectId, plan.rooms || []);
      loadRangeAndArchitect(projectId);
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
