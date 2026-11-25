import { escapeHtml, fmt } from "./api.js";

const LOAD_COLORS = ["#E3EEF4", "#C9DDE9", "#A9C9DC", "#86B3CE", "#6F9FBF"];

function pageBounds(page, outlines) {
  const points = outlines.flatMap((outline) => outline.points || []);
  const xs = points.map((point) => Number(point[0]));
  const ys = points.map((point) => Number(point[1]));
  const imageWidth = Number(page.image_width_px);
  const imageHeight = Number(page.image_height_px);
  const maxX = imageWidth || Math.max(1, ...xs);
  const maxY = imageHeight || Math.max(1, ...ys);
  if (!points.length) return { x: 0, y: 0, width: maxX, height: maxY };

  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const pointMaxX = Math.max(...xs);
  const pointMaxY = Math.max(...ys);
  const paddingX = Math.max(24, (pointMaxX - minX) * 0.04);
  const paddingY = Math.max(24, (pointMaxY - minY) * 0.04);
  const x = Math.max(0, minX - paddingX);
  const y = Math.max(0, minY - paddingY);
  return {
    x,
    y,
    width: Math.min(maxX - x, pointMaxX + paddingX - x),
    height: Math.min(maxY - y, pointMaxY + paddingY - y),
  };
}

function loadColor(value, values) {
  if (!Number.isFinite(Number(value))) return LOAD_COLORS[0];
  const low = Math.min(...values);
  const high = Math.max(...values);
  const position = high === low ? 0 : (Number(value) - low) / (high - low);
  return LOAD_COLORS[
    Math.min(LOAD_COLORS.length - 1, Math.floor(position * 5))
  ];
}

function polygonArea(points) {
  let sum = 0;
  points.forEach((point, index) => {
    const next = points[(index + 1) % points.length];
    sum += Number(point[0]) * Number(next[1]) - Number(next[0]) * Number(point[1]);
  });
  return Math.abs(sum) / 2;
}

function boxOf(points) {
  const xs = points.map((point) => Number(point[0]));
  const ys = points.map((point) => Number(point[1]));
  return { width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys) };
}

function segmentDistance(point, start, end) {
  const [px, py] = point.map(Number);
  const [ax, ay] = start.map(Number);
  const [bx, by] = end.map(Number);
  const length = (bx - ax) ** 2 + (by - ay) ** 2;
  const t = length ? Math.max(0, Math.min(1, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / length)) : 0;
  return Math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)));
}

// The wall between two traced rooms: rooms are traced to their own face of each wall, so neighbours stand apart by
// the wall's thickness. Each wall's gap is measured from its middle to the nearest other room; the typical (median)
// gap no more than 1.5% of the plan's width (further away isn't a neighbour) sizes the band drawn under the rooms, so
// a shared wall reads as one wall instead of two lines with an empty strip between them (Casa Nova's shop and dining,
// 13 November 2026). The median, not the widest: one wide gap (a duct, a stair void) drew every wall of the
// Turramurra house twice as thick as drawn (20 November 2026).
export function wallWidth(outlines, planWidth) {
  const limit = planWidth * 0.015;
  const gaps = [];
  outlines.forEach((outline, index) => {
    outline.points.forEach((start, edge) => {
      const end = outline.points[(edge + 1) % outline.points.length];
      const middle = [(Number(start[0]) + Number(end[0])) / 2, (Number(start[1]) + Number(end[1])) / 2];
      let nearest = Infinity;
      outlines.forEach((other, otherIndex) => {
        if (otherIndex === index) return;
        other.points.forEach((a, otherEdge) => {
          nearest = Math.min(nearest, segmentDistance(middle, a, other.points[(otherEdge + 1) % other.points.length]));
        });
      });
      if (nearest > 0.5 && nearest <= limit) gaps.push(nearest);
    });
  });
  gaps.sort((a, b) => a - b);
  const typical = gaps.length ? gaps[Math.floor(gaps.length / 2)] : planWidth * 0.006;
  return Math.min(Math.max(typical * 1.1, planWidth * 0.003), planWidth * 0.012);
}

// The room's name as shown on its level's plan: "Living (Ground Floor)" is "Living" under a Ground Floor heading.
function shownName(outline, heading) {
  const name = String(outline.display_name || outline.name || "");
  // "Dining (Lower Ground Floor)" on the "Lower Ground" plan reads as "Dining": the plan's heading names the floor.
  const match = heading ? name.match(/^(.*\S)\s*\(([^)]+)\)$/) : null;
  if (!match) return name;
  const floor = (text) => text.toLowerCase().replace(/\bfloor\b|\blevel\b/g, "").replace(/\s+/g, " ").trim();
  return floor(match[2]) === floor(heading) ? match[1] : name;
}

function roomDetail(room, shadeLoads) {
  if (shadeLoads) return room.final_w_m2 == null ? "" : `${fmt(room.final_w_m2, 0)} W/m²`;
  return room.area_m2 == null ? "" : `${fmt(room.area_m2)} m²`;
}

// A label about 2.6% of the plan's width, smaller in a small room; null when even the smallest size would not fit
// (the room is then listed under the plan instead of crowding it).
function labelSize(outline, name, planWidth) {
  const box = boxOf(outline.points);
  const fit = (lines) => {
    const characters = Math.max(3, ...lines.map((line) => line.length));
    const size = Math.min(planWidth * 0.026, (box.width * 0.82) / (characters * 0.58), (box.height * 0.26 * 2) / (lines.length + 1));
    return size >= planWidth * 0.011 ? { size, lines } : null;
  };
  const words = name.split(" ");
  // A long name in a narrow room goes on two lines before it gives up its label.
  const split = words.length > 1
    ? words.map((_, index) => [words.slice(0, index + 1).join(" "), words.slice(index + 1).join(" ")]).slice(0, -1)
        .sort((a, b) => Math.max(a[0].length, a[1].length) - Math.max(b[0].length, b[1].length))[0]
    : null;
  return fit([name]) || (split ? fit(split) : null);
}

function outlineLabel(point, name, detail, label) {
  const [x, y] = point;
  const { size, lines } = label;
  const rows = lines.length + (detail ? 1 : 0);
  const top = y - size * 0.575 * (rows - 1) + size * 0.35;
  const names = lines.map((line, index) => `<tspan class="job-plan-name" x="${x}"${index ? ' dy="1.15em"' : ""}>${escapeHtml(line)}</tspan>`).join("");
  return `<text class="job-plan-label" x="${x}" y="${top}" text-anchor="middle" style="font-size:${size.toFixed(1)}px">${names}${detail ? `<tspan class="job-plan-detail" x="${x}" dy="1.15em" style="font-size:${(size * 0.78).toFixed(1)}px">${escapeHtml(detail)}</tspan>` : ""}</text>`;
}

// A numbered dot for a room too small to label; the number is keyed under the plan.
function outlineMarker(point, number, planWidth) {
  const [x, y] = point.map(Number);
  const r = planWidth * 0.011;
  return `<g class="job-plan-marker" aria-hidden="true"><circle cx="${x}" cy="${y}" r="${r.toFixed(1)}"/><text x="${x}" y="${(y + r * 0.38).toFixed(1)}" text-anchor="middle" style="font-size:${(r * 1.1).toFixed(1)}px">${number}</text></g>`;
}

function centreOf(points) {
  const xs = points.map((point) => Number(point[0]));
  const ys = points.map((point) => Number(point[1]));
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
}

function renderPage(plan, page, options) {
  // Largest room first, so a room traced inside a bigger one (a kitchen inside a shop) is drawn on top of it.
  const outlines = (plan.outlines || [])
    .filter((outline) => outline.page === page.page && outline.points?.length >= 3)
    .sort((a, b) => polygonArea(b.points) - polygonArea(a.points));
  const rooms = plan.rooms || [];
  const values = rooms
    .map((room) => Number(room.final_w_m2))
    .filter(Number.isFinite);
  const bounds = options.bounds || pageBounds(page, outlines);
  const traced = outlines.filter((outline) => outline.line_style !== "dotted");
  // Sizes are set against the widest floor shown, so labels and walls match from floor to floor.
  const scaleWidth = options.scaleWidth || bounds.width;
  const wall = wallWidth(traced, scaleWidth);
  const patternId = `refrigeration-${page.page}`;
  const heading = page.level_name || outlines.find((outline) => outline.level)?.level || "";
  const unlabelled = [];
  const seen = new Set();
  let total = 0;
  const roomOf = (outline) => rooms.find((item) => item.room_id === outline.room_id) || outline;
  // Every room's walls first, then every room over them: a wall shared by two rooms reads as one wall.
  const walls = traced
    .map((outline) => `<polygon class="job-plan-wall" points="${outline.points.map((point) => `${point[0]},${point[1]}`).join(" ")}" style="stroke-width:${wall.toFixed(1)}px" aria-hidden="true"/>`)
    .join("");
  // A joined room (pieces the drawing reader split) is labelled once, on its largest piece on this page, with the
  // joined name, area and load; its other pieces carry no label of their own.
  const joined = new Map((plan.joined_rooms || []).map((group) => [group.id, group]));
  const labelHolder = new Map();
  for (const outline of outlines) {
    if (!outline.join_id || !joined.has(outline.join_id)) continue;
    const holder = labelHolder.get(outline.join_id);
    if (!holder || polygonArea(outline.points) > polygonArea(holder.points)) labelHolder.set(outline.join_id, outline);
  }
  const polygons = outlines
    .map((outline) => {
      const group = outline.join_id ? joined.get(outline.join_id) : null;
      const piece = roomOf(outline);
      const room = group ? { ...piece, area_m2: group.area_m2, final_w_m2: group.final_w_m2 } : piece;
      const name = group ? group.name : shownName(outline, heading);
      const key = group ? group.id : outline.room_id;
      if (!seen.has(key)) {
        seen.add(key);
        if (Number.isFinite(Number(room.area_m2))) total += Number(room.area_m2);
      }
      const silent = group && labelHolder.get(group.id) !== outline;
      const classes = [
        outline.line_style === "dashed" ? "assumed" : "",
        outline.line_style === "dotted" ? "estimated" : "",
        outline.kind === "refrigeration" ? "refrigeration" : "",
        outline.room_id === options.selectedRoom ? "selected" : "",
        group ? "joined" : "",
        options.shadeLoads ? "load-shaded" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const fill =
        outline.kind === "refrigeration"
          ? `url(#${patternId})`
          : options.shadeLoads
            ? loadColor(room.final_w_m2, values)
            : "";
      const points = outline.points.map((point) => `${point[0]},${point[1]}`).join(" ");
      const level = outline.level ? `, ${escapeHtml(outline.level)}` : "";
      const detail = roomDetail(room, options.shadeLoads);
      const size = !silent && outline.label_point ? labelSize(outline, name, scaleWidth) : null;
      let marker = "";
      if (!size && !silent) {
        let row = unlabelled.find((item) => item.name === name);
        if (!row) {
          row = { name, detail, number: unlabelled.length + 1 };
          unlabelled.push(row);
        }
        marker = outlineMarker(outline.label_point || centreOf(outline.points), row.number, scaleWidth);
      }
      return `<g class="job-plan-room"><polygon class="${classes}" points="${points}"${fill ? ` fill="${fill}" style="fill:${fill}"` : ""} data-room-id="${escapeHtml(outline.room_id)}" role="img" aria-label="${escapeHtml(name)}${level}"/><title>${escapeHtml(name)}${level}${detail ? ` · ${escapeHtml(detail)}` : ""}</title>${size ? outlineLabel(outline.label_point, name, detail, size) : marker}</g>`;
    })
    .join("");
  const count = seen.size;
  const summary = `${count} room${count === 1 ? "" : "s"}${total > 0 && !options.shadeLoads ? ` · ${fmt(total)} m²` : ""}`;
  const title = `<figcaption><span class="job-plan-level">${escapeHtml(heading || `Plan page ${page.page}`)}</span><span class="job-plan-summary">${escapeHtml(summary)}</span></figcaption>`;
  const small = unlabelled.length
    ? `<p class="job-plan-small"><span>Numbered on the plan:</span> ${unlabelled.map((row) => `<span class="job-plan-chip" data-plan-key="${row.number}"><b class="job-plan-key-num" aria-hidden="true">${row.number}</b>${escapeHtml(row.name)}${row.detail ? ` <small>${escapeHtml(row.detail)}</small>` : ""}</span>`).join(" ")}</p>`
    : "";
  const accessibleName = outlines.map((outline) => shownName(outline, heading)).join(", ");
  return `<figure class="job-plan-page">${title}<svg class="job-plan" viewBox="${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}"${options.widthShare && options.widthShare < 0.999 ? ` style="width:${(options.widthShare * 100).toFixed(1)}%"` : ""} role="img" aria-label="Plan page ${page.page}${page.title ? `: ${escapeHtml(page.title)}` : ""}${accessibleName ? `, rooms: ${escapeHtml(accessibleName)}` : ""}" xmlns="http://www.w3.org/2000/svg"><defs><pattern id="${patternId}" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="#9AAAB3" stroke-width="2"/></pattern></defs><g class="job-plan-walls">${walls}</g>${polygons}</svg>${small}</figure>`;
}

export function planSvg(
  plan,
  {
    pageNumber = null,
    selectedRoom = "",
    onSelect = null,
    shadeLoads = false,
  } = {},
) {
  const availablePages = (plan.pages || []).filter((page) =>
    (plan.outlines || []).some(
      (outline) => outline.page === page.page && outline.points?.length >= 3,
    ),
  );
  const pages =
    pageNumber == null
      ? availablePages
      : availablePages.filter((page) => page.page === pageNumber);
  if (!pages.length) {
    return `<p class="job-muted">No room outlines are available yet.</p>`;
  }
  // Floors shown together share one scale: a 27 m² basement is drawn small beside a 220 m² ground floor, not
  // stretched to the same width.
  const bounds = pages.map((page) =>
    pageBounds(page, (plan.outlines || []).filter((outline) => outline.page === page.page && outline.points?.length >= 3)));
  const widest = Math.max(...bounds.map((box) => box.width));
  const markup = `<div class="job-plans">${pages
    .map((page, index) =>
      renderPage(plan, page, { selectedRoom, shadeLoads, bounds: bounds[index], scaleWidth: widest, widthShare: pages.length > 1 ? bounds[index].width / widest : 1 }))
    .join("")}</div>`;
  requestAnimationFrame(() => {
    document.querySelectorAll(".job-plan [data-room-id]").forEach((element) => {
      element.addEventListener("click", () =>
        onSelect?.(element.dataset.roomId),
      );
    });
  });
  return markup;
}

export function lineLegend() {
  return `<div class="job-legend" aria-label="Plan evidence legend"><span><i class="job-key"></i>Measured from the drawing</span><span><i class="job-key dashed"></i>Assumed: outline or scale not checked against a printed dimension</span><span><i class="job-key dotted"></i>AI estimate or room not traced</span></div>`;
}

export function roomList(plan) {
  const rooms = plan.rooms || [];
  if (!rooms.length) {
    return `<p class="job-muted">Room names will appear as the drawing set is read.</p>`;
  }
  return `<ul class="job-room-list">${rooms
    .map((room) => {
      const area =
        room.area_m2 == null
          ? "Area not available yet"
          : `${fmt(room.area_m2)} m²`;
      const pages = room.source_pages?.length
        ? `; drawing page${room.source_pages.length === 1 ? "" : "s"} ${room.source_pages.join(", ")}`
        : "";
      const refrigeration =
        room.kind === "refrigeration" ? " · Refrigeration" : "";
      return `<li><strong>${escapeHtml(room.display_name || room.name)}${refrigeration}</strong><small>${area}${pages}</small></li>`;
    })
    .join("")}</ul>`;
}
