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

function outlineLabel(outline, room, shadeLoads) {
  const point = outline.label_point;
  if (!point) return "";
  const label = escapeHtml(outline.name);
  const detail = shadeLoads
    ? room.final_w_m2 == null
      ? ""
      : `${fmt(room.final_w_m2, 0)} W/m²`
    : room.area_m2 == null
      ? ""
      : `${fmt(room.area_m2)} m²`;
  return `<text x="${point[0]}" y="${point[1]}" text-anchor="middle"><tspan>${label}</tspan>${detail ? `<tspan x="${point[0]}" dy="1.2em" font-size="28" font-weight="400">${escapeHtml(detail)}</tspan>` : ""}</text>`;
}

function renderPage(plan, page, options) {
  const outlines = (plan.outlines || []).filter(
    (outline) => outline.page === page.page && outline.points?.length >= 3,
  );
  const rooms = plan.rooms || [];
  const values = rooms
    .map((room) => Number(room.final_w_m2))
    .filter(Number.isFinite);
  const bounds = pageBounds(page, outlines);
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
      return `<g><polygon class="${classes}" points="${points}" fill="${fill}" data-room-id="${escapeHtml(outline.room_id)}" aria-label="${escapeHtml(outline.name)}${level}"/><title>${escapeHtml(outline.name)}${level}</title>${outlineLabel(outline, room, options.shadeLoads)}</g>`;
    })
    .join("");
  const heading =
    page.level_name || outlines.find((outline) => outline.level)?.level;
  const title = heading
    ? `<figcaption>${escapeHtml(heading)}</figcaption>`
    : "";
  const accessibleName = outlines.map((outline) => outline.name).join(", ");
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
      return `<li><strong>${escapeHtml(room.name)}${refrigeration}</strong><small>${area}${pages}</small></li>`;
    })
    .join("")}</ul>`;
}
