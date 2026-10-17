import { getJson, sendJson, escapeHtml, navigate } from "./api.js";
export async function renderJobDetails(projectId) {
  const status = await getJson(
    `/api/job-status?project_id=${encodeURIComponent(projectId)}`,
  );
  const weather = (status.weather_locations || [])
    .map(
      (place) =>
        `<option value="${escapeHtml(place)}" ${place === status.weather_location ? "selected" : ""}>${escapeHtml(place)}</option>`,
    )
    .join("");
  const content = `<section class="job-panel"><a class="job-link-button" href="#/job/${encodeURIComponent(projectId)}/${status.stage || "drawings"}">Back to the job</a><h1>Job details</h1><form id="job-details-form"><label class="job-field"><span>Job name</span><input name="name" value="${escapeHtml(status.name || "")}" required></label><label class="job-field"><span>Site address</span><input name="address" value="${escapeHtml(status.address || status.found_site || "")}"></label><label class="job-field"><span>Building type</span><input name="building_type" value="${escapeHtml(status.building_type || "")}"></label><label class="job-field"><span>What is above the tenancy?</span><select name="above"><option value="">Not identified</option><option value="roof" ${status.above === "roof" ? "selected" : ""}>The roof</option><option value="floor" ${status.above === "floor" ? "selected" : ""}>Another floor or tenancy</option><option value="not_sure" ${status.above === "not_sure" ? "selected" : ""}>Not sure</option></select></label>${weather ? `<label class="job-field"><span>Weather location</span><select name="weather_location"><option value="">Use the AI selection</option>${weather}</select></label>` : `<p class="job-note">${status.weather_ai_pick ? `Picked by the AI: ${escapeHtml(status.weather_ai_pick)}${status.weather_ai_why ? ` (${escapeHtml(status.weather_ai_why)})` : ""}` : "Weather location has not been identified yet."}</p>`}<button class="job-button">Save details</button><p class="job-note" id="job-details-message" role="status"></p></form></section>`;
  return {
    status,
    content,
    statusLine:
      "Update the project details used with its drawings and weather data.",
    bind: () =>
      document
        .querySelector("#job-details-form")
        ?.addEventListener("submit", async (event) => {
          event.preventDefault();
          const form = event.currentTarget,
            button = form.querySelector("button");
          button.disabled = true;
          try {
            await sendJson("/api/job-setup", {
              project_id: projectId,
              name: form.elements.name.value,
              address: form.elements.address.value,
              building_type: form.elements.building_type.value,
              above: form.elements.above.value,
              weather_location: form.elements.weather_location?.value || "",
              edited_by: "Job workspace",
            });
            document.querySelector("#job-details-message").textContent =
              "Saved.";
            setTimeout(
              () => navigate(projectId, status.stage || "drawings"),
              500,
            );
          } catch (error) {
            document.querySelector("#job-details-message").textContent =
              `Could not save: ${error.message}`;
            button.disabled = false;
          }
        }),
  };
}
