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
// the wall's thickness. Each wall's gap is measured from its middle to the nearest other room; the widest gap no
// more than 2.5% of the plan's width (further away isn't a neighbour) sizes the grey band drawn under the rooms, so a
// shared wall reads as one wall instead of two lines with an empty strip between them (Casa Nova's shop and dining,
// 13 November 2026).
export function wallWidth(outlines, planWidth) {
  const limit = planWidth * 0.025;
  let widest = 0;
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
      if (nearest > 0.5 && nearest <= limit) widest = Math.max(widest, nearest);
    });
  });
  return Math.min(Math.max((widest || planWidth * 0.008) * 1.1, planWidth * 0.004), planWidth * 0.03);
}

function outlineLabel(outline, room, shadeLoads, size = 38) {
  const point = outline.label_point;
  if (!point) return "";
  const label = escapeHtml(outline.display_name || outline.name);
  const detail = shadeLoads
    ? room.final_w_m2 == null
      ? ""
      : `${fmt(room.final_w_m2, 0)} W/m²`
    : room.area_m2 == null
      ? ""
      : `${fmt(room.area_m2)} m²`;
  return `<text x="${point[0]}" y="${point[1]}" text-anchor="middle" style="font-size:${size.toFixed(1)}px"><tspan>${label}</tspan>${detail ? `<tspan x="${point[0]}" dy="1.2em" style="font-size:${(size * 0.75).toFixed(1)}px" font-weight="400">${escapeHtml(detail)}</tspan>` : ""}</text>`;
}

// A label about 3% of the plan's width, made smaller to fit a small room (its name across about 85% of the room).
function labelSize(outline, planWidth) {
  const box = boxOf(outline.points);
  const characters = Math.max(4, String(outline.display_name || outline.name || "").length);
  return Math.max(planWidth * 0.012, Math.min(planWidth * 0.03, (box.width * 0.85) / (characters * 0.6), box.height * 0.3));
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
  const bounds = pageBounds(page, outlines);
  const traced = outlines.filter((outline) => outline.line_style !== "dotted");
  const wall = wallWidth(traced, bounds.width);
  const patternId = `refrigeration-${page.page}`;
  const polygons = outlines
    .map((outline) => {
      const room =
        rooms.find((item) => item.room_id === outline.room_id) || outline;
      const classes = [
        outline.line_style === "dashed" ? "assumed" : "",
        outline.line_style === "dotted" ? "estimated" : "",
        outline.kind === "refrigeration" ? "refrigeration" : "",
        outline.room_id === options.selectedRoom ? "selected" : "",
        options.shadeLoads ? "load-shaded" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const fill =
        outline.kind === "refrigeration"
          ? `url(#${patternId})`
          : options.shadeLoads
            ? loadColor(room.final_w_m2, values)
            : "#FFFFFF";
      const points = outline.points
        .map((point) => `${point[0]},${point[1]}`)
        .join(" ");
      const level = outline.level ? `, ${escapeHtml(outline.level)}` : "";
      // The room's walls first (a band half inside, half outside its outline), then the room over them.
      const band = outline.line_style === "dotted"
        ? ""
        : `<polygon class="job-plan-wall" points="${points}" style="stroke-width:${wall.toFixed(1)}px" aria-hidden="true"/>`;
      return `<g>${band}<polygon class="${classes}" points="${points}" fill="${fill}" data-room-id="${escapeHtml(outline.room_id)}" aria-label="${escapeHtml(outline.display_name || outline.name)}${level}"/><title>${escapeHtml(outline.display_name || outline.name)}${level}</title>${outlineLabel(outline, room, options.shadeLoads, labelSize(outline, bounds.width))}</g>`;
    })
    .join("");
  const heading =
    page.level_name || outlines.find((outline) => outline.level)?.level;
  const title = heading
    ? `<figcaption>${escapeHtml(heading)}</figcaption>`
    : "";
  const accessibleName = outlines.map((outline) => outline.display_name || outline.name).join(", ");
  return `<figure class="job-plan-page">${title}<svg class="job-plan" viewBox="${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}" role="img" aria-label="Plan page ${page.page}${page.title ? `: ${escapeHtml(page.title)}` : ""}${accessibleName ? `, rooms: ${escapeHtml(accessibleName)}` : ""}" xmlns="http://www.w3.org/2000/svg"><defs><pattern id="${patternId}" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="#9AAAB3" stroke-width="2"/></pattern></defs>${polygons}</svg></figure>`;
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
  const markup = `<div class="job-plans">${pages
    .map((page) => renderPage(plan, page, { selectedRoom, shadeLoads }))
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
  return `<div class="job-legend" aria-label="Plan evidence legend"><span><i class="job-key"></i>Measured from the drawing</span><span><i class="job-key dashed"></i>Assumed</span><span><i class="job-key dotted"></i>AI estimate or room not traced</span></div>`;
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
