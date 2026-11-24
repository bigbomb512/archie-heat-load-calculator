export async function getJson(url) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}
export async function sendJson(url, data) {
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(data),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(body.error || `Request failed (${response.status}).`);
  return body;
}
export const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
export const fmt = (value, digits = 1) =>
  Number.isFinite(Number(value))
    ? Number(value).toLocaleString("en-AU", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "Not found yet";
export function route() {
  const parts = location.hash
    .replace(/^#\/?/, "")
    .split("/")
    .filter(Boolean)
    .map(decodeURIComponent);
  if (parts[0] === "job") {
    const old = parts[2] || "drawings";
    const redirects = { project: "details", walls: "rooms", windows: "rooms" };
    let step = redirects[old] || old;
    if (old === "drawings" && parts[3]) step = "drawings";
    if (
      old === "rooms" &&
      parts[3] &&
      !["phase1", "measurements"].includes(parts[3])
    )
      return { projectId: parts[1] || "", step: "rooms", roomId: parts[3] };
    return { projectId: parts[1] || "", step, roomId: parts[3] || "" };
  }
  if (parts[0] === "jobs") return { projectId: "", step: "jobs", roomId: "" };
  if (parts[0] === "new-job")
    return { projectId: "", step: "new-job", roomId: "" };
  return { projectId: "", step: "jobs", roomId: "" };
}
export function navigate(projectId, step = "drawings", roomId = "") {
  const path = projectId
    ? `#/job/${encodeURIComponent(projectId)}/${step}${roomId ? `/${encodeURIComponent(roomId)}` : ""}`
    : "#/jobs";
  if (location.hash === path)
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = path;
}

// The result label as a short badge ("Design", "Preliminary · 4 open"); the full sentence is its tooltip and is shown
// in full where there is room (the Results headline).
export function labelBadge(label) {
  if (!label?.text || label.status === "not_calculated") return "";
  const design = label.status === "design";
  const short = design ? "Design" : `Preliminary${Number(label.count) > 0 ? ` · ${Number(label.count)} open` : ""}`;
  return `<span class="job-answer-badge ${design ? "ok" : "warn"}" data-result-label title="${escapeHtml(label.text)}${label.reason ? ` — ${escapeHtml(label.reason)}` : ""}">${escapeHtml(short)}</span>`;
}

// Assumption text from the server carries dated references for the audit trail ("(user decision 2026-10-30)",
// "(Phase 1 rule, 2026-10-06)", "; review 2026-11-17"). On screen they are noise: drop them, keep the sentence.
export function plainNote(text) {
  return String(text ?? "")
    .replace(/\s*\((?:[^()]*?\b(?:user decision|decision|review|rule|reviewed)\b[^()]*?)?\d{4}-\d{2}-\d{2}[^()]*\)/gi, "")
    .replace(/[;,]?\s*\b(?:user decision|review)\s+\d{4}-\d{2}-\d{2}\b/gi, "")
    .replace(/\s+([:;,.])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
}
