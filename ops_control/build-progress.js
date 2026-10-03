/* Same-origin polling; command output is always rendered as text, never HTML. */
(() => {
  "use strict";
  const job = document.getElementById("build-job");
  if (!job) return;
  const output = document.getElementById("build-output");
  const follow = document.getElementById("build-follow");
  const status = document.getElementById("build-status");
  const connection = document.getElementById("build-connection");
  const progress = document.getElementById("build-progress");
  let cursor = Number(job.dataset.cursor);
  let finished = false;
  follow.addEventListener("change", () => {
    if (follow.checked) output.scrollTop = output.scrollHeight;
  });
  async function poll() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/build-status?id=" + encodeURIComponent(job.dataset.job) + "&cursor=" + cursor,
        {credentials: "same-origin", cache: "no-store", signal: controller.signal});
      if (response.status === 401 || response.status === 403) {
        connection.textContent = "Session expired. Reopen myCamino Control to reconnect; do not start another build.";
        finished = true;
        return;
      }
      if (response.status === 404) {
        connection.textContent = "This progress session is no longer available. The panel may have restarted. Check its status before starting another build.";
        finished = true;
        return;
      }
      if (!response.ok) throw new Error("Progress request failed");
      const data = await response.json();
      if (data.reset) output.textContent = data.output;
      else output.append(document.createTextNode(data.output));
      if (output.textContent.length > 250000) output.textContent = output.textContent.slice(-250000);
      cursor = data.cursor;
      progress.max = data.total;
      progress.value = data.completed;
      document.getElementById("build-stage").textContent = data.stage;
      document.getElementById("build-count").textContent = data.completed + " of " + data.total + " stages completed";
      const minutes = Math.floor(data.elapsed / 60);
      document.getElementById("build-elapsed").textContent = "Elapsed: " + minutes + "m " + (data.elapsed % 60) + "s";
      document.getElementById("build-truncated").textContent = data.truncated ? "Showing the latest output; older lines have been trimmed." : "";
      connection.textContent = "";
      finished = data.status !== "running";
      const statusText = !finished ? "Building… live updates every second" :
        (data.status === "succeeded" ? "Completed successfully — DMG saved in dist/myCamino-GPS-Track-Show.dmg" : "Build failed (exit " + data.returncode + "). See the output below.");
      if (status.textContent !== statusText) status.textContent = statusText;
      status.className = !finished ? "" : (data.status === "succeeded" ? "status-ok" : "status-bad");
      progress.classList.toggle("build-failed", data.status === "failed");
      if (follow.checked) output.scrollTop = output.scrollHeight;
    } catch (_) {
      connection.textContent = "Connection interrupted. Reconnecting… The build may still be running; do not start it again.";
    } finally {
      clearTimeout(timeout);
      if (!finished) setTimeout(poll, 1000);
    }
  }
  poll();
})();
