// Metadata-driven fixed research forms. Financial validation remains server-owned.
export function createResearchForms(ui) {
  const ORIGIN = { kind: "inline_form", path_base: "repository_root", form_schema_version: 1 };
  const schemaNames = ["symbol", "timestamp", "open", "high", "low", "close", "volume"];

  class DraftError extends Error {
    constructor(field, message) { super(message); this.field = field; }
  }

  function clone(value) { return JSON.parse(JSON.stringify(value)); }

  function freeze(value) {
    if (value && typeof value === "object" && !Object.isFrozen(value)) {
      Object.freeze(value);
      for (const child of Object.values(value)) freeze(child);
    }
    return value;
  }

  function labelFor(path) {
    return path.split(".").at(-1).replaceAll("_", " ").replace(/^./, (value) => value.toUpperCase());
  }

  function section(title, path) {
    const node = ui.element("fieldset", { class: "research-form-section", "data-field-path": path });
    node.append(ui.element("legend", { text: title }));
    return node;
  }

  function secureKey() {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return `qte_${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
  }

  function validateOptions(options) {
    const origin = options?.config_origin;
    if (options?.schema_version !== 1 || !origin
        || origin.kind !== ORIGIN.kind || origin.path_base !== ORIGIN.path_base
        || origin.form_schema_version !== ORIGIN.form_schema_version
        || !options.research_run || !options.strategy_lab) {
      throw new Error("Research form metadata is unavailable.");
    }
    return options;
  }

  function createController(kind, options, request, selectedData = null, prerequisites = null) {
    const metadata = options[kind];
    const isDownload = kind === "download_alpaca";
    const isData = isDownload || kind === "dataset_validate";
    const launchAllowed = !isDownload || (prerequisites?.enabled === true && prerequisites?.credentials_present === true);
    const state = {
      revision: 0, preview: null, intent: null, uncertain: false,
      previewing: false, submitting: false, fields: new Map(),
    };
    const title = isDownload ? "Alpaca historical data" : kind === "dataset_validate" ? "Validate local dataset" : kind === "research_run" ? "New research run" : "New synthetic strategy lab";
    const form = ui.element("form", { class: "research-form" });
    const draft = ui.element("fieldset", { class: "research-form-draft" });
    draft.append(ui.element("legend", { class: "visually-hidden", text: `${title} configuration` }));
    const error = ui.element("p", { class: "research-form-error", role: "alert", tabindex: "-1" });
    error.hidden = true;
    const previewArea = ui.element("section", { class: "research-form-preview", "aria-live": "polite" });
    const previewButton = ui.element("button", { type: "submit", text: "Preview assumptions" });
    const launchButton = ui.element("button", { type: "button", text: isDownload ? "Download historical data" : "Launch fixed job" });
    launchButton.disabled = true;
    const retryButton = ui.element("button", { type: "button", text: "Retry same request" });
    retryButton.hidden = true;
    const reviewButton = ui.element("button", { type: "button", text: "Review recent jobs" });
    reviewButton.hidden = true;
    const newButton = ui.element("button", { type: "button", text: "Start a new run" });
    newButton.hidden = true;
    const actions = ui.element("div", { class: "research-form-actions" }, [
      previewButton, launchButton, retryButton, reviewButton, newButton,
    ]);
    form.append(draft, error, previewArea, actions);

    function register(path, node) {
      node.setAttribute("data-field-path", path);
      state.fields.set(path, node);
      return node;
    }

    function showError(problem, fallback = "The request was not accepted.") {
      const message = typeof problem?.message === "string" && problem.message ? problem.message : fallback;
      const field = typeof problem?.field === "string" ? problem.field.slice(0, 160) : "";
      error.textContent = field ? `${field}: ${message}` : message;
      error.hidden = false;
      error.focus({ preventScroll: true });
      for (const node of state.fields.values()) node.removeAttribute("aria-invalid");
      let current = field;
      while (current) {
        const target = state.fields.get(current);
        if (target) {
          for (let ancestor = target.parentElement; ancestor; ancestor = ancestor.parentElement) {
            if (ancestor.tagName === "DETAILS") ancestor.open = true;
          }
          target.setAttribute("aria-invalid", "true");
          target.focus({ preventScroll: false });
          break;
        }
        const parent = current.replace(/\[[0-9]+\]$/, "").replace(/\.[^.]+$/, "");
        if (parent === current) break;
        current = parent;
      }
    }

    function clearError() {
      error.hidden = true;
      error.textContent = "";
      for (const node of state.fields.values()) node.removeAttribute("aria-invalid");
    }

    function invalidateDraft() {
      if (state.submitting) return;
      state.revision += 1;
      state.preview = null;
      state.intent = null;
      state.uncertain = false;
      previewArea.replaceChildren();
      launchButton.disabled = true;
      retryButton.hidden = true;
      reviewButton.hidden = true;
      newButton.hidden = true;
      clearError();
    }

    function scalar(path, descriptor, settings = {}) {
      const required = settings.required ?? descriptor?.required === true;
      const nullable = settings.nullable ?? descriptor?.nullable === true;
      const wrapper = ui.element("div", { class: "research-form-field", "data-field-path": path });
      const label = ui.element("label", { class: "field-label" }, [
        ui.element("span", { text: settings.label || labelFor(path) }),
      ]);
      let input;
      if (descriptor?.type === "boolean") {
        input = ui.element("input", { type: "checkbox", name: path });
      } else if (descriptor?.type === "choice") {
        input = ui.element("select", { name: path });
        for (const choice of descriptor.choices || []) {
          input.append(ui.element("option", { value: choice, text: settings.choiceLabel?.(choice) || labelFor(choice) }));
        }
      } else if (descriptor?.type === "json_text") {
        input = ui.element("textarea", { name: path, rows: "8", autocomplete: "off", maxlength: "12000" });
        input.value = descriptor.default || "";
        wrapper.append(ui.element("p", { class: "muted", text: "Explicit local router profile. Quote timestamps, durations and lookback integers as decimal strings. Missing macro inputs mean cash. Dealer observations are not inferred from prices." }),
          ui.textLink("/library/macro-router", "Router profile documentation"));
      } else {
        input = ui.element("input", {
          type: "text", name: path, autocomplete: "off",
          inputmode: descriptor?.type === "number" ? "decimal" : descriptor?.type === "integer_string" ? "numeric" : "text",
        });
        if (descriptor?.type === "integer_string") {
          input.setAttribute("pattern", settings.signed ? "(?:0|-[1-9][0-9]*|[1-9][0-9]*)" : "(?:0|[1-9][0-9]*)");
        }
        if (Object.hasOwn(descriptor || {}, "default")) input.value = String(descriptor.default);
      }
      input.required = required && descriptor?.type !== "boolean";
      register(path, input);
      label.append(input);

      let include = null;
      let mode = null;
      if (!required && nullable) {
        mode = ui.element("select", { class: "research-form-null-mode", "aria-label": `${labelFor(path)} mode` });
        mode.append(
          ui.element("option", { value: "omit", text: "Use default (omit)" }),
          ui.element("option", { value: "value", text: "Set a value" }),
          ui.element("option", { value: "null", text: "Set null" }),
        );
        mode.addEventListener("change", () => { input.disabled = mode.value !== "value"; });
        input.disabled = true;
        wrapper.append(mode);
      } else if (!required) {
        const includeLabel = ui.element("label", { class: "research-form-include" });
        include = ui.element("input", { type: "checkbox" });
        includeLabel.append(include, globalThis.document.createTextNode(" Include override"));
        include.addEventListener("change", () => { input.disabled = !include.checked; });
        input.disabled = true;
        wrapper.append(includeLabel);
      }
      wrapper.prepend(label);

      function read() {
        if (mode?.value === "omit" || include && !include.checked) return { present: false };
        if (mode?.value === "null") return { present: true, value: null };
        if (descriptor?.type === "boolean") {
          if (settings.mustBeTrue && !input.checked) throw new DraftError(path, "This acknowledgement is required.");
          return { present: true, value: input.checked };
        }
        const value = input.value;
        if (!value) throw new DraftError(path, "A value is required.");
        if (descriptor?.type === "integer_string") {
          const pattern = settings.signed ? /^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$/ : /^(?:0|[1-9][0-9]*)$/;
          if (!pattern.test(value)) throw new DraftError(path, "Use a canonical decimal string.");
          return { present: true, value };
        }
        if (descriptor?.type === "number") {
          if (value.trim() !== value) throw new DraftError(path, "Use a finite JSON number without surrounding whitespace.");
          const number = Number(value);
          if (!Number.isFinite(number)) throw new DraftError(path, "Use a finite JSON number.");
          return { present: true, value: number };
        }
        return { present: true, value };
      }
      return { wrapper, input, read, include, mode };
    }

    function addScalars(parent, fields) {
      const values = {};
      for (const [name, field] of Object.entries(fields)) {
        values[name] = scalar(field.path, field.descriptor, field.settings);
        parent.append(values[name].wrapper);
      }
      return values;
    }

    function readObject(values) {
      const result = {};
      for (const [name, field] of Object.entries(values)) {
        const read = field.read();
        if (read.present) result[name] = read.value;
      }
      return result;
    }

    function choices(path, available, defaults, required = false) {
      const wrapper = section(labelFor(path), path);
      wrapper.classList.add("research-form-checks");
      let include = null;
      if (!required) {
        const includeLabel = ui.element("label", { class: "research-form-include" });
        include = ui.element("input", { type: "checkbox" });
        includeLabel.append(include, globalThis.document.createTextNode(" Include override"));
        wrapper.append(includeLabel);
      }
      const inputs = [];
      for (const value of available || []) {
        const label = ui.element("label");
        const input = ui.element("input", { type: "checkbox", value });
        input.checked = Array.isArray(defaults) && defaults.includes(value);
        if (!required) input.disabled = true;
        label.append(input, globalThis.document.createTextNode(` ${labelFor(value)}`));
        wrapper.append(label);
        inputs.push(input);
      }
      if (include) include.addEventListener("change", () => {
        for (const input of inputs) input.disabled = !include.checked;
      });
      register(path, wrapper);
      return {
        wrapper,
        read() {
          if (include && !include.checked) return { present: false };
          const value = inputs.filter((input) => input.checked).map((input) => input.value);
          if (required && !value.length) throw new DraftError(path, "Select at least one value.");
          return { present: true, value };
        },
      };
    }

    function sampling(fields) {
      const wrapper = section("Sampling and annualization", "configuration.sampling");
      const enabledLabel = ui.element("label", { class: "research-form-include" });
      const enabled = ui.element("input", { type: "checkbox" });
      enabledLabel.append(enabled, globalThis.document.createTextNode(" Enable sampling"));
      const body = ui.element("fieldset", { class: "research-form-grid" }, [
        ui.element("legend", { class: "visually-hidden", text: "Sampling configuration" }),
      ]);
      const gridLabel = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Grid" })]);
      const grid = ui.element("select", { name: "configuration.sampling.grid" }, [
        ui.element("option", { value: "interval", text: "Fixed interval" }),
        ui.element("option", { value: "timestamps", text: "Explicit timestamps" }),
      ]);
      gridLabel.append(grid);
      const interval = scalar("configuration.sampling.interval_ns", fields["sampling.interval_ns"], { required: true });
      const timestampsWrap = ui.element("label", { class: "field-label research-form-field", "data-field-path": "configuration.sampling.timestamps_ns" }, [
        ui.element("span", { text: "Timestamps (one canonical integer per line)" }),
      ]);
      const timestamps = register("configuration.sampling.timestamps_ns", ui.element("textarea", {
        name: "configuration.sampling.timestamps_ns", rows: "6", autocomplete: "off",
      }));
      timestampsWrap.append(timestamps);
      const staleness = scalar("configuration.sampling.max_staleness_ns", fields["sampling.max_staleness_ns"], { required: true });
      const periods = scalar("configuration.sampling.periods_per_year", fields["sampling.periods_per_year"], { required: true });
      const riskFree = scalar("configuration.sampling.annual_risk_free_rate", fields["sampling.annual_risk_free_rate"]);
      body.append(gridLabel, interval.wrapper, timestampsWrap, staleness.wrapper, periods.wrapper, riskFree.wrapper);
      wrapper.append(enabledLabel, body);

      function update() {
        body.hidden = !enabled.checked;
        body.disabled = !enabled.checked;
        interval.wrapper.hidden = grid.value !== "interval";
        interval.input.disabled = !enabled.checked || grid.value !== "interval";
        timestampsWrap.hidden = grid.value !== "timestamps";
        timestamps.disabled = !enabled.checked || grid.value !== "timestamps";
      }
      enabled.addEventListener("change", update);
      grid.addEventListener("change", update);
      register("configuration.sampling", enabled);
      update();
      return {
        wrapper,
        read() {
          if (!enabled.checked) return null;
          const result = {
            max_staleness_ns: staleness.read().value,
            periods_per_year: periods.read().value,
          };
          if (grid.value === "interval") result.interval_ns = interval.read().value;
          else {
            const values = timestamps.value.split(/\r?\n/);
            if (!values.length || values.some((value) => !/^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$/.test(value))) {
              throw new DraftError("configuration.sampling.timestamps_ns", "Use one canonical signed integer per line.");
            }
            result.timestamps_ns = values;
          }
          const optional = riskFree.read();
          if (optional.present) result.annual_risk_free_rate = optional.value;
          return result;
        },
      };
    }

    let buildConfiguration;
    if (kind === "research_run" || kind === "dataset_validate") {
      const fields = metadata.fields;
      const dataSection = section("Captured repository data", "configuration.data");
      const dataCore = addScalars(dataSection, {
        adapter: { path: "configuration.data.adapter", descriptor: fields["data.adapter"], settings: { required: true } },
        path: { path: "configuration.data.path", descriptor: fields["data.path"], settings: { required: true } },
        interval_ns: { path: "configuration.data.interval_ns", descriptor: fields["data.interval_ns"], settings: { required: true } },
        tick_size: { path: "configuration.data.tick_size", descriptor: fields["data.tick_size"] },
        currency: { path: "configuration.data.currency", descriptor: fields["data.currency"] },
      });
      const csvOnly = section("CSV declarations", "configuration.data.schema");
      const actionFree = scalar("configuration.data.action_free", fields["data.action_free"], {
        required: true, mustBeTrue: true, label: "I declare this interval action-free",
      });
      actionFree.input.required = true;
      csvOnly.append(actionFree.wrapper);
      const schemaFields = {};
      const schemaOverrides = ui.element("details", {}, [ui.element("summary", { text: "Optional CSV column mapping" })]);
      for (const name of schemaNames) {
        schemaFields[name] = scalar(`configuration.data.schema.${name}`, fields["data.schema.*"]);
        schemaOverrides.append(schemaFields[name].wrapper);
      }
      csvOnly.append(schemaOverrides);
      function updateAdapter() {
        const csv = dataCore.adapter.input.value === "csv";
        csvOnly.hidden = !csv;
        csvOnly.disabled = !csv;
      }
      dataCore.adapter.input.addEventListener("change", updateAdapter);
      updateAdapter();

      if (selectedData) {
        dataSection.append(ui.element("p", { text: "Using a validated source with a pinned checksum. If its bytes change, preview and launch will reject it. Start a new run to choose different data." }),
          ui.element("pre", { text: JSON.stringify(selectedData, null, 2) }));
        dataSection.disabled = true;
        csvOnly.disabled = true;
        csvOnly.hidden = true;
        for (const name of ["adapter", "path", "interval_ns", "tick_size", "currency"]) {
          if (Object.hasOwn(selectedData, name)) dataCore[name].input.value = String(selectedData[name]);
        }
      }
      function readData() {
        if (selectedData) return clone(selectedData);
        const data = readObject(dataCore);
        if (data.adapter === "csv") {
          data.action_free = actionFree.read().value;
          const schema = readObject(schemaFields);
          if (Object.keys(schema).length) data.schema = schema;
        }
        return data;
      }
      if (kind === "dataset_validate") {
        draft.append(dataSection, csvOnly);
        buildConfiguration = () => ({ data: readData() });
      } else {

      const strategySection = section("Registered strategy", "configuration.strategy");
      const strategyType = scalar("configuration.strategy.type", fields["strategy.type"], { required: true });
      const symbol = scalar("configuration.strategy.symbol", fields["strategy.symbol"], { required: true });
      strategySection.append(strategyType.wrapper, symbol.wrapper);
      const strategyPanels = new Map();
      for (const strategy of metadata.strategies || []) {
        const panel = ui.element("fieldset", { class: "research-form-grid" });
        panel.append(ui.element("legend", { class: "visually-hidden", text: `${strategy.label} parameters` }));
        const values = {};
        for (const name of strategy.exposed_fields || []) {
          values[name] = scalar(`configuration.strategy.${name}`, strategy.fields?.[name] || {}, {
            required: strategy.type === "moving_average_regime" || strategy.type === "macro_router",
          });
          panel.append(values[name].wrapper);
        }
        strategyPanels.set(strategy.type, { panel, values });
        strategySection.append(panel);
      }
      function updateStrategy() {
        for (const key of [...state.fields.keys()]) {
          if (key.startsWith("configuration.strategy.")
              && key !== "configuration.strategy.type" && key !== "configuration.strategy.symbol") {
            state.fields.delete(key);
          }
        }
        for (const [name, value] of strategyPanels) {
          value.panel.hidden = name !== strategyType.input.value;
          value.panel.disabled = name !== strategyType.input.value;
        }
        const selected = strategyPanels.get(strategyType.input.value);
        if (selected) {
          for (const [name, value] of Object.entries(selected.values)) {
            state.fields.set(`configuration.strategy.${name}`, value.input);
          }
        }
      }
      strategyType.input.addEventListener("change", updateStrategy);
      updateStrategy();
      state.fields.set("configuration.strategy", strategyType.input);
      state.fields.set("configuration.data", dataCore.adapter.input);

      const engineSection = section("Engine", "configuration.engine");
      const engineCore = addScalars(engineSection, {
        initial_cash: { path: "configuration.engine.initial_cash", descriptor: fields["engine.initial_cash"], settings: { required: true } },
      });
      const engineAdvancedSection = section("Advanced engine", "configuration.engine.advanced");
      const engineAdvanced = addScalars(engineAdvancedSection, {
        history_capacity: { path: "configuration.engine.history_capacity", descriptor: fields["engine.history_capacity"] },
        random_seed: { path: "configuration.engine.random_seed", descriptor: fields["engine.random_seed"] },
      });
      const executionSection = section("Execution costs", "configuration.execution");
      const execution = {};
      for (const name of ["commission_bps", "spread_bps", "slippage_bps"]) {
        execution[name] = scalar(`configuration.execution.${name}`, fields[`execution.${name}`]);
        executionSection.append(execution[name].wrapper);
      }
      const riskSection = section("Risk limits", "configuration.risk");
      const risk = {};
      for (const name of ["max_order_quantity", "max_symbol_allocation", "max_gross_leverage", "allow_short", "cash_floor"]) {
        risk[name] = scalar(`configuration.risk.${name}`, fields[`risk.${name}`]);
        riskSection.append(risk[name].wrapper);
      }
      const sample = sampling(fields);
      draft.append(dataSection, csvOnly, strategySection, engineSection,
                   ui.element("details", {}, [ui.element("summary", { text: "Advanced engine" }), engineAdvancedSection]),
                   ui.element("details", {}, [ui.element("summary", { text: "Advanced execution" }), executionSection]),
                   ui.element("details", {}, [ui.element("summary", { text: "Advanced risk" }), riskSection]),
                   ui.element("details", {}, [ui.element("summary", { text: "Optional sampling" }), sample.wrapper]));

      buildConfiguration = () => {
        const data = readData();
        const selected = strategyPanels.get(strategyType.input.value);
        if (!selected) throw new DraftError("configuration.strategy.type", "Select a registered strategy.");
        const strategy = { type: strategyType.input.value, symbol: symbol.read().value, ...readObject(selected.values) };
        const configuration = { data, strategy, engine: { ...readObject(engineCore), ...readObject(engineAdvanced) } };
        const executionValues = readObject(execution);
        const riskValues = readObject(risk);
        if (Object.keys(executionValues).length) configuration.execution = executionValues;
        if (Object.keys(riskValues).length) configuration.risk = riskValues;
        const samplingValue = sample.read();
        if (samplingValue) configuration.sampling = samplingValue;
        return configuration;
      };
      }
    } else if (isDownload) {
      const requestSection = section("Explicit historical request", "configuration");
      const fields = metadata.fields || {};
      const controls = {};
      for (const name of ["symbols", "timeframe", "start", "end", "feed", "action_free"]) {
        const descriptor = name === "symbols" ? { type: "string" } : fields[name];
        controls[name] = scalar(`configuration.${name}`, descriptor, {
          required: true, mustBeTrue: name === "action_free",
          label: ({ symbols: "Symbols (comma separated, uppercase)", start: "Start (UTC RFC3339)",
            end: "End inclusive (UTC RFC3339)", action_free: "I declare this window free of corporate actions" })[name],
        });
        if (name === "feed") {
          controls[name].input.prepend(ui.element("option", { value: "", text: "Select an entitled feed" }));
          controls[name].input.value = "";
        }
        requestSection.append(controls[name].wrapper);
      }
      draft.append(requestSection);
      buildConfiguration = () => {
        const configuration = readObject(controls);
        configuration.symbols = configuration.symbols.split(",").map(value => value.trim());
        return configuration;
      };
    } else {
      const fields = metadata.fields;
      const coreSection = section("Synthetic fixture", "configuration");
      const core = addScalars(coreSection, {
        seed: { path: "configuration.seed", descriptor: fields.seed, settings: { required: true } },
        bars_per_window: { path: "configuration.bars_per_window", descriptor: fields.bars_per_window, settings: { required: true } },
      });
      const timeframes = choices("configuration.timeframes", metadata.timeframes, [], true);
      const regimes = choices("configuration.regimes", metadata.regimes, metadata.defaults?.regimes, false);
      const strategySection = section("Shared strategy parameters", "configuration.strategy_parameters");
      const strategy = {};
      for (const [name, descriptor] of Object.entries(metadata.strategy_parameter_fields || {})) {
        strategy[name] = scalar(`configuration.strategy_parameters.${name}`, descriptor);
        strategySection.append(strategy[name].wrapper);
      }
      const groups = {};
      for (const [name, paths] of Object.entries({
        engine: ["initial_cash", "max_symbol_allocation", "max_gross_leverage", "max_order_quantity"],
        execution: ["commission_bps", "spread_bps", "slippage_bps"],
        stress_execution: ["commission_bps", "spread_bps", "slippage_bps"],
        selection: ["min_closed_trades", "min_validation_windows", "min_stressed_return", "worst_stressed_return", "max_drawdown"],
      })) {
        const group = section(labelFor(name), `configuration.${name}`);
        const values = {};
        for (const field of paths) {
          values[field] = scalar(`configuration.${name}.${field}`, fields[`${name}.${field}`]);
          group.append(values[field].wrapper);
        }
        groups[name] = { group, values };
      }
      draft.append(coreSection, timeframes.wrapper, regimes.wrapper,
        ui.element("details", {}, [ui.element("summary", { text: "Shared strategy overrides" }), strategySection]),
        ...Object.entries(groups).map(([name, value]) => ui.element("details", {}, [
          ui.element("summary", { text: labelFor(name) }), value.group,
        ])));

      buildConfiguration = () => {
        const configuration = { schema_version: 1, ...readObject(core) };
        configuration.timeframes = timeframes.read().value;
        const regimeValue = regimes.read();
        if (regimeValue.present) configuration.regimes = regimeValue.value;
        const strategyValues = readObject(strategy);
        if (Object.keys(strategyValues).length) configuration.strategy_parameters = strategyValues;
        for (const [name, value] of Object.entries(groups)) {
          const values = readObject(value.values);
          if (Object.keys(values).length) configuration[name] = values;
        }
        return configuration;
      };
    }

    const timeout = scalar("timeout_seconds", { type: "integer_string", required: true, default: "1800" }, { required: true, label: "Timeout seconds" });
    draft.append(timeout.wrapper);

    function envelope(configuration) {
      return {
        schema_version: 1, kind, config_origin: clone(ORIGIN), configuration: clone(configuration),
      };
    }

    function renderPreview(result) {
      const assumptions = ui.element("ol", { class: "research-form-assumptions" });
      for (const value of Array.isArray(result.assumptions) ? result.assumptions : []) {
        assumptions.append(ui.element("li", { text: String(value) }));
      }
      const effective = ui.element("details", { class: "research-form-config" }, [
        ui.element("summary", { text: "Server-built effective configuration" }),
        ui.element("pre", { text: JSON.stringify(result.effective_configuration, null, 2) }),
      ]);
      previewArea.replaceChildren(
        ui.element("h2", { text: "Previewed assumptions" }), assumptions, effective,
        ui.element("p", { class: "muted", text: "Preview is not proof that data is untouched and does not reserve these inputs." }),
      );
    }

    async function preview() {
      if (state.previewing || state.submitting || state.uncertain) return;
      clearError();
      let configuration;
      try { configuration = buildConfiguration(); }
      catch (problem) { showError(problem, "Complete the form before previewing."); return; }
      const revision = state.revision;
      const body = envelope(configuration);
      state.preview = null;
      state.intent = null;
      previewArea.replaceChildren();
      launchButton.disabled = true;
      state.previewing = true;
      previewButton.disabled = true;
      ui.setStatus("Validating captured inputs and assumptions.");
      try {
        const result = await ui.api("/api/v1/configs/validate", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
        if (!ui.isCurrentView(request) || revision !== state.revision) return;
        if (!/^[0-9a-f]{64}$/.test(result?.preview_fingerprint || "")) {
          throw new Error("The preview response is incomplete.");
        }
        state.preview = freeze({ revision, configuration: clone(configuration), result: clone(result) });
        state.intent = null;
        renderPreview(result);
        launchButton.disabled = !launchAllowed;
        ui.setStatus("");
      } catch (problem) {
        if (ui.isCurrentView(request) && revision === state.revision) {
          state.preview = null;
          launchButton.disabled = true;
          ui.setStatus("");
          showError(problem, "Preview was not accepted.");
        }
      } finally {
        state.previewing = false;
        if (ui.isCurrentView(request) && !state.submitting && !state.uncertain) previewButton.disabled = false;
      }
    }

    function lockDraft(value) {
      draft.disabled = value;
      launchButton.disabled = value || !state.preview || !launchAllowed;
      previewButton.disabled = value || state.previewing;
    }

    function newIntent() {
      const timeoutValue = timeout.read().value;
      const timeoutNumber = Number(timeoutValue);
      if (!Number.isSafeInteger(timeoutNumber) || timeoutNumber < 1 || timeoutNumber > 1800) {
        throw new DraftError("timeout_seconds", "Use an integer from 1 through 1800.");
      }
      return freeze({
        ...envelope(state.preview.configuration),
        preview_fingerprint: state.preview.result.preview_fingerprint,
        idempotency_key: secureKey(), timeout_seconds: timeoutNumber,
      });
    }

    async function launch() {
      if (!launchAllowed) return;
      if (state.previewing || state.submitting || !state.preview && !state.intent) return;
      clearError();
      if (!state.intent) {
        try { state.intent = newIntent(); }
        catch (problem) { showError(problem); return; }
      }
      state.submitting = true;
      lockDraft(true);
      retryButton.hidden = true;
      reviewButton.hidden = true;
      newButton.hidden = true;
      ui.setStatus("Submitting the fixed local job.");
      try {
        const result = await ui.api("/api/v1/jobs", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.intent),
        });
        if (!ui.isCurrentView(request)) return;
        const id = result?.job?.id;
        if (typeof id !== "string" || !id) throw new Error("The job response did not identify a job.");
        ui.navigate(`/research/jobs/${encodeURIComponent(id)}`);
      } catch (problem) {
        if (!ui.isCurrentView(request)) return;
        ui.setStatus("");
        const definitiveRejection = Number.isInteger(problem?.status)
          && problem.status >= 400 && problem.status < 500;
        const retryOfUncertainIntent = state.uncertain;
        if (definitiveRejection && !retryOfUncertainIntent) {
          state.intent = null;
          state.preview = null;
          state.uncertain = false;
          lockDraft(false);
          previewArea.replaceChildren();
          showError(problem, "Job submission was rejected. Preview this draft again.");
        } else {
          state.uncertain = true;
          launchButton.disabled = true;
          previewButton.disabled = true;
          retryButton.hidden = definitiveRejection;
          reviewButton.hidden = false;
          newButton.hidden = false;
          error.textContent = definitiveRejection
            ? "This retry was rejected, but the earlier submission may already exist. Review recent jobs before starting a new run, which could create a duplicate."
            : "Submission outcome is unknown. A job may already exist. Retry sends the exact same request and idempotency key; starting a new run could create a duplicate.";
          error.hidden = false;
          error.focus({ preventScroll: true });
        }
      } finally {
        state.submitting = false;
      }
    }

    form.addEventListener("input", invalidateDraft);
    form.addEventListener("change", invalidateDraft);
    form.addEventListener("submit", (event) => { event.preventDefault(); preview(); });
    launchButton.addEventListener("click", launch);
    retryButton.addEventListener("click", launch);
    reviewButton.addEventListener("click", () => ui.navigate("/research"));
    newButton.addEventListener("click", () => {
      if (state.submitting) return;
      state.intent = null;
      state.preview = null;
      state.uncertain = false;
      state.revision += 1;
      retryButton.hidden = true;
      reviewButton.hidden = true;
      newButton.hidden = true;
      previewArea.replaceChildren();
      lockDraft(false);
      launchButton.disabled = true;
      clearError();
      ui.setStatus("Draft retained. Preview again before starting a new run.");
    });

    ui.replaceView(
      ui.element("p", { class: "muted" }, [ui.textLink(isData ? "/data" : "/research", isData ? "Data" : "Research")]),
      ui.element("h1", { text: title }),
      ui.element("p", { class: "lede", text: isDownload
        ? "Preview is offline. Only an explicit download submits a provider request. Raw USD prices; no calendar validation or corporate-action verification."
        : kind === "dataset_validate"
        ? "Validate captured local data through the existing adapters. No strategy, trading, data repair or exchange-calendar validation."
        : kind === "research_run"
        ? "Configure one registered strategy against one captured repository input."
        : "Configure the fixed synthetic mechanics lab. Candidates and evidence are server-built." }),
      ...(isDownload && !launchAllowed ? [ui.element("p", { class: "muted", text:
        "Downloads are blocked. Server permission and locally configured credentials are both required. No credentials belong in this form." })] : []),
      form,
    );
    ui.setStatus("");
  }

  async function load(kind, datasetId = null) {
    const request = ui.beginView();
    ui.setNavigation(["dataset_validate", "download_alpaca"].includes(kind) ? "data" : "research");
    ui.setStatus("Loading fixed research form metadata.");
    try {
      const options = validateOptions(await ui.api("/api/v1/research/form-options"));
      if (!ui.isCurrentView(request)) return;
      if (!options[kind]) throw new Error("Form metadata is unavailable.");
      let prerequisites = null;
      if (kind === "download_alpaca") {
        prerequisites = await ui.api("/api/v1/data/alpaca/prerequisites");
        if (!ui.isCurrentView(request)) return;
      }
      let selectedData = null;
      if (datasetId !== null) {
        if (!/^[0-9a-f]{32}$/.test(datasetId)) throw new Error("Invalid dataset selection.");
        const selected = await ui.api(`/api/v1/datasets/${encodeURIComponent(datasetId)}`);
        if (!ui.isCurrentView(request)) return;
        if (selected?.dataset?.source?.state !== "available" || !selected.dataset.source.data) {
          throw new Error("The validated source is unavailable or its bytes changed. Validate the new source first.");
        }
        selectedData = freeze(clone(selected.dataset.source.data));
      }
      createController(kind, options, request, selectedData, prerequisites);
    } catch (problem) {
      if (!ui.isCurrentView(request)) return;
      ui.setStatus("");
      ui.replaceView(
        ui.element("h1", { text: "Research form unavailable" }),
        ui.element("p", { class: "error", text: problem?.message || "Form metadata could not be loaded." }),
        ui.textLink("/research", "Return to Research"),
      );
    }
  }

  return { loadRun: (datasetId = null) => load("research_run", datasetId), loadLab: () => load("strategy_lab"), loadDataset: () => load("dataset_validate"), loadDownload: () => load("download_alpaca") };
}
