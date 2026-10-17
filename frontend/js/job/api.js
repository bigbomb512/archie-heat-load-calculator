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
