/* Offline-only connection prerequisites.  This view never contacts a broker. */
export function createSystemConnections(ui) {
  let epoch = 0;
  const endpoint = "/api/v1/connections";
  const providers = [
    {
      id: "schwab", label: "Schwab", scope: "Contract-defined GET only",
      credentialEnvironmentKey: "SCHWAB_ACCESS_TOKEN", contractState: "not_configured",
      signerState: "not_applicable", operations: [],
      fixedBlockers: ["broker_reads_disabled", "contract_not_configured"],
    },
    {
      id: "robinhood_crypto", label: "Robinhood Crypto", scope: "Crypto GET only; no equities or trading",
      credentialEnvironmentKey: "ROBINHOOD_CRYPTO_API_KEY", contractState: "not_applicable",
      signerState: "not_configured", operations: ["accounts", "trading_pairs", "holdings", "orders"],
      fixedBlockers: ["broker_reads_disabled", "signer_not_configured"],
    },
  ];

  const humanize = value => ui.humanize ? ui.humanize(value) : String(value).replaceAll("_", " ");
  const active = (ticket, request) => ticket === epoch && ui.isCurrentView(request);
  const sameStrings = (actual, expected) => Array.isArray(actual) && actual.length === expected.length && actual.every((item, index) => item === expected[index]);

  function dispose() { epoch += 1; }

  // Deliberately accept only the narrow, local readiness DTO.  Unknown values
  // never reach the DOM, including in an error message.
  function credentialPresence(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
      payload.schema_version !== 1 || payload.mode !== "offline_prerequisites_only" ||
      payload.read_enabled !== false || payload.private_response_retention !== false ||
      !Array.isArray(payload.connections) || payload.connections.length !== providers.length) return null;
    const byId = new Map(payload.connections.map(item => [item?.id, item]));
    if (byId.size !== providers.length) return null;
    const result = new Map();
    for (const provider of providers) {
      const item = byId.get(provider.id);
      const blockers = item?.credential_key_present === false
        ? [...provider.fixedBlockers, "credential_key_absent"] : provider.fixedBlockers;
      if (!item || typeof item !== "object" || Array.isArray(item) ||
        item.label !== provider.label || item.scope !== provider.scope ||
        item.credential_environment_key !== provider.credentialEnvironmentKey ||
        typeof item.credential_key_present !== "boolean" ||
        item.credential_validity !== "not_checked" || item.authentication !== "not_verified" ||
        item.read_enabled !== false || item.contract_state !== provider.contractState ||
        item.signer_state !== provider.signerState || !sameStrings(item.operations, provider.operations) ||
        !sameStrings(item.blockers, blockers)) return null;
      result.set(provider.id, item.credential_key_present);
    }
    return result;
  }

  function detailRows(provider, present) {
    const presence = present === true ? "Environment key present (value not inspected)" :
      present === false ? "Environment key absent" : "Environment key presence unavailable";
    const rows = [
      ["Scope", provider.scope],
      ["Credential environment key", provider.credentialEnvironmentKey],
      ["Credential presence", presence],
      ["Credential validity", "Not checked"],
      ["Authentication", "Not verified"],
      ["Reads", "Disabled"],
      ["Contract", humanize(provider.contractState)],
      ["Signer", humanize(provider.signerState)],
      ["Operations", provider.operations.length ? provider.operations.map(humanize).join(", ") : "None"],
      ["Blockers", [...provider.fixedBlockers, ...(present === false ? ["credential_key_absent"] : [])].map(humanize).join(", ")],
    ];
    const list = ui.element("dl", { class: "job-meta" });
    for (const [label, value] of rows) list.append(ui.element("dt", { text: label }), ui.element("dd", { text: value }));
    return list;
  }

  async function load() {
    const request = ui.beginView();
    const ticket = ++epoch;
    ui.setNavigation("system");
    ui.setStatus("Loading offline connection prerequisites.");
    let presence = null;
    let notice;
    let cards;
    let refresh;
    let refreshing = false;

    const guarded = () => active(ticket, request);
    const setNotice = (message, error = false) => {
      if (!notice) return;
      notice.textContent = message;
      notice.className = error ? "error" : "muted";
      notice.hidden = !message;
    };
    const render = () => {
      if (!cards) return;
      cards.replaceChildren();
      for (const provider of providers) {
        const card = ui.element("section", { class: "connection-card", "aria-labelledby": `connection-${provider.id}` });
        card.append(
          ui.element("h2", { id: `connection-${provider.id}`, text: provider.label }),
          ui.element("p", { class: "lede", text: "Blocked — not connected" }),
          ui.element("details", {}, [ui.element("summary", { text: "Offline prerequisites" }), detailRows(provider, presence?.get(provider.id))]),
        );
        cards.append(card);
      }
    };
    const refreshStatus = async () => {
      if (!guarded() || refreshing) return;
      refreshing = true;
      refresh.disabled = true;
      ui.setStatus("Refreshing offline connection prerequisites.");
      try {
        const payload = await ui.api(endpoint);
        if (!guarded()) return;
        const next = credentialPresence(payload);
        if (!next) {
          presence = null;
          setNotice("Connection status could not be verified. All providers remain blocked.", true);
          ui.setStatus("Offline connection prerequisites unavailable.");
        } else {
          presence = next;
          setNotice("Offline prerequisites refreshed.");
          ui.setStatus("Offline connection prerequisites loaded.");
        }
        render();
      } catch (_) {
        if (!guarded()) return;
        presence = null;
        setNotice("Connection status could not be verified. All providers remain blocked.", true);
        render();
        ui.setStatus("Offline connection prerequisites unavailable.");
      } finally {
        refreshing = false;
        if (guarded() && refresh) refresh.disabled = false;
      }
    };

    const root = ui.element("section", { class: "system-connections", "aria-labelledby": "system-connections-heading" });
    notice = ui.element("p", { class: "muted" }); notice.hidden = true;
    cards = ui.element("div", { class: "connection-cards" });
    refresh = ui.element("button", { type: "button", text: "Refresh connection status" });
    refresh.addEventListener("click", refreshStatus);
    root.append(
      ui.element("p", { class: "muted" }, [ui.textLink("/system", "System")]),
      ui.element("h1", { id: "system-connections-heading", text: "Connections" }),
      ui.element("p", { class: "lede", text: "Offline prerequisites only. No broker requests are made." }),
      refresh,
      notice,
      cards,
      ui.element("p", { class: "muted" }, [ui.textLink("/library/broker-connections", "Broker connections documentation")]),
    );
    render();
    ui.replaceView(root);
    await refreshStatus();
  }

  return { load, dispose };
}
