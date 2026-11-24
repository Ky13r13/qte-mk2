/* Schwab token status, private refresh form and weekly data collection.
   The app key and secret are sent once to the local GUI process and the inputs are
   cleared immediately; nothing is stored in the browser. */
export function createSchwabToken(ui) {
  let epoch = 0;
  let poll = null;

  function dispose() {
    epoch += 1;
    if (poll) { clearInterval(poll); poll = null; }
  }

  const minutes = seconds => `${Math.floor(seconds / 60)} min ${seconds % 60} s`;

  function tokenText(token) {
    if (!token || token.present !== true) return token?.reason || "No Schwab authorization saved.";
    if (token.expired) return "Access token expired. Refresh below.";
    return `Access token valid for ${minutes(token.seconds_left)} (expires ${new Date(token.expires_at * 1000).toLocaleTimeString()}).`;
  }

  function collectText(collect) {
    if (!collect || collect.state === "idle") return "Not run in this GUI session.";
    if (collect.state === "running") return "Collecting all intervals from Schwab… (about 1–3 minutes)";
    if (collect.state === "failed") return `Collection failed (${collect.error}). Check the token and try again.`;
    return `Done: ${collect.ok}/${collect.requests} requests ingested, ${collect.errors} errors, ${collect.derived_series} series re-derived. Captures in ${collect.capture_dir}.`;
  }

  async function load() {
    dispose();
    const ticket = epoch;
    const request = ui.beginView();
    ui.setNavigation("system");
    const tokenLine = ui.element("p", { text: "Loading token status…" });
    const collectLine = ui.element("p", { class: "muted", text: "" });
    const message = ui.element("p", { role: "status" });
    const key = ui.element("input", { type: "password", id: "schwab-app-key", autocomplete: "off", spellcheck: "false" });
    const secret = ui.element("input", { type: "password", id: "schwab-app-secret", autocomplete: "off", spellcheck: "false" });
    const refreshButton = ui.element("button", { type: "submit", text: "Refresh token" });
    const form = ui.element("form", { class: "job-form" }, [
      ui.element("label", { for: "schwab-app-key", text: "Schwab app key" }), key,
      ui.element("label", { for: "schwab-app-secret", text: "Schwab app secret" }), secret,
      refreshButton,
    ]);
    const collectButton = ui.element("button", { type: "button", text: "Run weekly collect" });

    async function refreshStatus() {
      try {
        const payload = await ui.api("/api/v1/schwab/token");
        if (ticket !== epoch || !ui.isCurrentView(request)) return;
        tokenLine.textContent = tokenText(payload.token);
        collectLine.textContent = collectText(payload.collect);
        collectButton.disabled = !(payload.token?.present && !payload.token.expired) || payload.collect?.state === "running";
        if (payload.collect?.state !== "running" && poll) { clearInterval(poll); poll = null; }
      } catch (error) {
        if (ticket === epoch) tokenLine.textContent = error.message;
      }
    }

    form.addEventListener("submit", async event => {
      event.preventDefault();
      const body = JSON.stringify({ schema_version: 1, app_key: key.value, app_secret: secret.value });
      key.value = ""; secret.value = "";  // never keep credentials in the page
      refreshButton.disabled = true;
      message.textContent = "Refreshing…";
      try {
        const result = await ui.api("/api/v1/schwab/token/refresh", {
          method: "POST", headers: { "Content-Type": "application/json" }, body,
        });
        message.textContent = `Refreshed. Valid for ${minutes(result.seconds_left)}.`;
      } catch (error) {
        message.textContent = error.message;
      } finally {
        refreshButton.disabled = false;
        refreshStatus();
      }
    });

    collectButton.addEventListener("click", async () => {
      collectButton.disabled = true;
      try {
        await ui.api("/api/v1/schwab/collect", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ schema_version: 1 }),
        });
        if (!poll) poll = setInterval(refreshStatus, 5000);
      } catch (error) {
        message.textContent = error.message;
      }
      refreshStatus();
    });

    ui.replaceView(
      ui.element("p", { class: "muted" }, [ui.textLink("/system", "System"), globalThis.document.createTextNode(" / Schwab")]),
      ui.element("h1", { text: "Schwab token and data" }),
      ui.element("p", { class: "lede", text: "Access tokens last about 30 minutes. Refresh here, then run the weekly collect within that window. Your app key and secret are used once by this local process and are never stored or shown." }),
      tokenLine,
      ui.element("h2", { text: "Refresh access token" }), form, message,
      ui.element("h2", { text: "Weekly data collection" }),
      ui.element("p", { class: "muted", text: "Downloads 1m–30m, daily, weekly and monthly history for SPX, SPY, ES, VIX and QQQ into data/truth, then rebuilds 1h/4h bars." }),
      collectButton, collectLine,
    );
    refreshStatus();
  }

  return { load, dispose };
}
