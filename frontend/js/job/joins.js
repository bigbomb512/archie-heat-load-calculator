// Rooms the drawing reader split into pieces ("Ensuite (1)" … "Ensuite (4)"), joined into one room and split again.
// POST /api/room-join (backend/room_join_service.py). The pieces keep their own walls, windows and roof in the
// calculation; joined, they are one room with one name, one use and one set of people.
import { sendJson, escapeHtml, fmt } from "./api.js";

const baseName = (name) => String(name || "").replace(/\s*\(\d+\)\s*$/, "").trim();
const numbered = (name) => /\(\d+\)\s*$/.test(String(name || ""));

// Same-level rooms sharing a base name, at least one of them numbered, none already joined.
export function joinCandidates(plan) {
  const groups = new Map();
  for (const room of plan.rooms || []) {
    if (room.join_id || room.kind === "not_a_room") continue;
    const key = `${String(room.level || "").toLowerCase()}|${baseName(room.name).toLowerCase()}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(room);
  }
  return [...groups.values()]
    .filter((rooms) => rooms.length > 1 && rooms.some((room) => numbered(room.name)))
    .map((rooms) => ({
      name: baseName(rooms[0].name),
      level: rooms[0].level || "",
      rooms,
      area: rooms.reduce((sum, room) => sum + (Number(room.area_m2) || 0), 0),
    }));
}

export function joinPanel(plan) {
  const candidates = joinCandidates(plan);
  const joined = plan.joined_rooms || [];
  if (!candidates.length && !joined.length) return "";
  const candidateRows = candidates
    .map(
      (group, index) => `<li data-join-candidate="${index}">
        <p><strong>${escapeHtml(group.name)}</strong>${group.level ? ` · ${escapeHtml(group.level)}` : ""} was read as ${group.rooms.length} pieces (${fmt(group.area)} m² together):
        ${group.rooms.map((room) => escapeHtml(room.display_name || room.name)).join(", ")}.</p>
        <span class="job-todo-field"><label><span class="job-sr-only">Name of the joined room</span><input type="text" maxlength="80" data-join-name value="${escapeHtml(group.name)}"></label>
        <button type="button" class="job-button secondary" data-join='${escapeHtml(JSON.stringify(group.rooms.map((room) => room.room_id)))}'>Join into one room</button></span>
      </li>`,
    )
    .join("");
  const joinedRows = joined
    .map(
      (group) => `<li data-joined="${escapeHtml(group.id)}">
        <p><strong>${escapeHtml(group.name)}</strong>${group.level ? ` · ${escapeHtml(group.level)}` : ""}: one room joined from ${(group.member_ids || []).length} pieces${Number.isFinite(Number(group.area_m2)) && group.area_m2 !== null ? `, ${fmt(group.area_m2)} m²` : ""}.</p>
        <button type="button" class="job-button secondary" data-split="${escapeHtml(group.id)}">Split into pieces</button>
      </li>`,
    )
    .join("");
  return `<section class="job-panel job-joins" data-join-panel>
    <h2>Rooms read in pieces</h2>
    <p class="job-caption">Join pieces that are one room: each piece keeps its walls, windows and roof, and the room gets one name, one use and its people counted once. Update the load afterwards.</p>
    ${candidateRows ? `<ul class="job-join-list">${candidateRows}</ul>` : ""}
    ${joinedRows ? `<h3>Joined rooms</h3><ul class="job-join-list">${joinedRows}</ul>` : ""}
    <p class="job-todo-message" data-join-message role="status"></p>
  </section>`;
}

export function bindJoins(projectId, onDone) {
  const panel = document.querySelector("[data-join-panel]");
  if (!panel) return;
  const message = panel.querySelector("[data-join-message]");
  const run = async (button, body) => {
    panel.querySelectorAll("button").forEach((control) => (control.disabled = true));
    try {
      const saved = await sendJson("/api/room-join", { project_id: projectId, reviewer: "Check the house", ...body });
      message.textContent = `${(saved.applied || []).join(" ") || "Saved."} Update the load to see the new total.`;
      onDone?.();
    } catch (error) {
      message.textContent = error.message;
      message.setAttribute("role", "alert");
      panel.querySelectorAll("button").forEach((control) => (control.disabled = false));
    }
  };
  panel.querySelectorAll("[data-join]").forEach((button) =>
    button.addEventListener("click", () => {
      const name = button.closest("li")?.querySelector("[data-join-name]")?.value.trim();
      if (!name) {
        message.textContent = "Give the joined room a name.";
        return;
      }
      run(button, { action: "join", room_ids: JSON.parse(button.dataset.join), name });
    }),
  );
  panel.querySelectorAll("[data-split]").forEach((button) =>
    button.addEventListener("click", () => run(button, { action: "split", join_id: button.dataset.split })),
  );
}
