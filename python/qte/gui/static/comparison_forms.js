// Metadata-driven comparison launch form. Preparation and financial validation remain server-owned.
export function createComparisonForms(ui) {
  const IDENTIFIER = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
  const VALIDATION_ID = /^[0-9a-f]{32}$/;

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

  function secureKey() {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return `qte_${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
  }

  function section(title, path) {
    const node = ui.element("fieldset", { class: "research-form-section", "data-field-path": path });
    node.append(ui.element("legend", { text: title }));
    return node;
  }

  function validateOptions(value) {
    if (value?.schema_version !== 1 || !Array.isArray(value.strategies) || !value.strategies.length
        || !Array.isArray(value.workflows) || !value.workflows.length) {
      throw new Error("Comparison form metadata is unavailable.");
    }
    const strategyTypes = new Set();
    for (const strategy of value.strategies) {
      if (!strategy || typeof strategy.type !== "string" || !strategy.type || strategyTypes.has(strategy.type)
          || typeof strategy.fields !== "object" || strategy.fields === null || Array.isArray(strategy.fields)
          || !Array.isArray(strategy.exposed_fields)) {
        throw new Error("Comparison form metadata is unavailable.");
      }
      strategyTypes.add(strategy.type);
    }
    if (!strategyTypes.has("moving_average_regime")) throw new Error("Comparison form metadata is unavailable.");
    const workflowTypes = new Set();
    for (const workflow of value.workflows) {
      if (!workflow || typeof workflow.type !== "string" || !workflow.type || workflowTypes.has(workflow.type)
          || !Number.isInteger(workflow.min_windows) || !Number.isInteger(workflow.max_windows)
          || workflow.min_windows < 2 || workflow.max_windows < workflow.min_windows || workflow.max_windows > 24
          || !Number.isInteger(workflow.max_candidates) || workflow.max_candidates < 1 || workflow.max_candidates > 32
          || typeof workflow.experiment_fields !== "object" || workflow.experiment_fields === null
          || Array.isArray(workflow.experiment_fields)) {
        throw new Error("Comparison form metadata is unavailable.");
      }
      workflowTypes.add(workflow.type);
    }
    if (!workflowTypes.has("moving_average_comparison") || !workflowTypes.has("historical_experiment")) {
      throw new Error("Comparison form metadata is unavailable.");
    }
    return value;
  }

  function createController(options, request) {
    const strategies = new Map(options.strategies.map((value) => [value.type, value]));
    const workflows = new Map(options.workflows.map((value) => [value.type, value]));
    const state = {
      revision: 0, preview: null, intent: null, uncertain: false,
      previewing: false, submitting: false, fields: new Map(), nextRow: 1,
      windows: [], candidates: [], chooserSequence: 0,
    };

    // JavaScript validation owns focus so invalid controls inside collapsed details are revealed.
    const form = ui.element("form", { class: "research-form", novalidate: "" });
    const draft = ui.element("fieldset", { class: "research-form-draft" });
    draft.append(ui.element("legend", { class: "visually-hidden", text: "Comparison configuration" }));
    const workflowSection = section("Workflow", "configuration.workflow");
    const workflowLabel = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Workflow" })]);
    const workflowInput = ui.element("select", { name: "configuration.workflow" });
    for (const value of options.workflows) {
      workflowInput.append(ui.element("option", { value: value.type, text: value.label || labelFor(value.type) }));
    }
    workflowInput.value = "moving_average_comparison";
    workflowLabel.append(workflowInput);
    workflowSection.append(workflowLabel);
    const windowsHost = ui.element("section", { "aria-label": "Ordered research windows" });
    const candidatesHost = ui.element("section", { "aria-label": "Ordered strategy candidates" });
    const experimentHost = ui.element("section", { "aria-label": "Experiment settings" });
    const timeoutField = makeScalar("timeout_seconds", {
      type: "integer_string", required: true, default: "1800",
    }, { label: "Timeout seconds", required: true });
    draft.append(workflowSection, windowsHost, candidatesHost, experimentHost, timeoutField.wrapper);

    const error = ui.element("p", { class: "research-form-error", role: "alert", tabindex: "-1" });
    error.hidden = true;
    const previewArea = ui.element("section", { class: "research-form-preview", "aria-live": "polite" });
    const previewButton = ui.element("button", { type: "submit", text: "Preview assumptions" });
    const launchButton = ui.element("button", { type: "button", text: "Launch fixed comparison" });
    launchButton.disabled = true;
    const retryButton = ui.element("button", { type: "button", text: "Retry same request" });
    retryButton.hidden = true;
    const reviewButton = ui.element("button", { type: "button", text: "Review recent jobs" });
    reviewButton.hidden = true;
    const newButton = ui.element("button", { type: "button", text: "Start a new comparison" });
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

    function clearError() {
      error.hidden = true;
      error.textContent = "";
      for (const node of state.fields.values()) node.removeAttribute("aria-invalid");
    }

    function showError(problem, fallback) {
      const field = typeof problem?.field === "string" ? problem.field.slice(0, 160) : "";
      error.textContent = field ? `${field}: ${fallback}` : fallback;
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

    function makeScalar(path, descriptor = {}, settings = {}) {
      const required = settings.required ?? descriptor.required === true;
      const hasDefault = Object.hasOwn(descriptor, "default");
      const wrapper = ui.element("div", { class: "research-form-field", "data-field-path": path });
      const label = ui.element("label", { class: "field-label" }, [
        ui.element("span", { text: settings.label || labelFor(path) }),
      ]);
      let input;
      if (descriptor.type === "boolean") {
        input = ui.element("input", { type: "checkbox", name: path });
        input.checked = hasDefault ? descriptor.default === true : false;
      } else if (descriptor.type === "choice") {
        input = ui.element("select", { name: path });
        if (!required && !hasDefault) input.append(ui.element("option", { value: "", text: "Use default" }));
        for (const choice of descriptor.choices || []) {
          input.append(ui.element("option", { value: choice, text: labelFor(choice) }));
        }
        if (hasDefault) input.value = String(descriptor.default);
      } else if (descriptor.type === "string_array") {
        input = ui.element("textarea", { name: path, rows: "4", autocomplete: "off" });
        if (hasDefault) input.value = Array.isArray(descriptor.default) ? descriptor.default.join("\n") : "";
      } else {
        input = ui.element("input", {
          type: "text", name: path, autocomplete: "off",
          inputmode: descriptor.type === "number" ? "decimal" : descriptor.type === "integer_string" ? "numeric" : "text",
        });
        if (descriptor.type === "integer_string") {
          input.setAttribute("pattern", descriptor.signed === true
            ? "(?:0|-[1-9][0-9]*|[1-9][0-9]*)" : "(?:0|[1-9][0-9]*)");
        }
        if (hasDefault) input.value = String(descriptor.default);
      }
      input.required = required && descriptor.type !== "boolean";
      register(path, input);
      label.append(input);
      wrapper.append(label);

      function read() {
        if (descriptor.type === "boolean") return { present: true, value: input.checked };
        if (descriptor.type === "string_array") {
          if (input.value === "" && required) throw new DraftError(path, "Enter at least one value.");
          if (input.value === "" && !hasDefault) return { present: false };
          if (input.value === "") return { present: true, value: [] };
          const values = input.value.split(/\r?\n/);
          if (values.some((value) => !value || value.trim() !== value)) {
            throw new DraftError(path, "Use one nonempty string per line without surrounding whitespace.");
          }
          return { present: true, value: values };
        }
        const value = input.value;
        if (!value) {
          if (!required && !hasDefault) return { present: false };
          throw new DraftError(path, "A value is required.");
        }
        if (descriptor.type === "integer_string") {
          const pattern = descriptor.signed === true
            ? /^(?:0|-[1-9][0-9]*|[1-9][0-9]*)$/ : /^(?:0|[1-9][0-9]*)$/;
          if (!pattern.test(value)) throw new DraftError(path, "Use a canonical decimal string.");
          return { present: true, value };
        }
        if (descriptor.type === "number") {
          if (value.trim() !== value) throw new DraftError(path, "Use a finite JSON number without surrounding whitespace.");
          const number = Number(value);
          if (!Number.isFinite(number)) throw new DraftError(path, "Use a finite JSON number.");
          return { present: true, value: number };
        }
        if (value.trim() !== value) throw new DraftError(path, "Remove surrounding whitespace.");
        return { present: true, value };
      }
      return { wrapper, input, read };
    }

    function rawScalar(field, descriptor = {}) {
      if (descriptor.type === "boolean") return field.input.checked;
      if (descriptor.type === "string_array") return field.input.value.split(/\r?\n/);
      return field.input.value;
    }

    function newWindow(role) {
      return {
        key: state.nextRow++, render: 0, controls: null,
        name: role, role, regime: "", validation_id: "",
        evidence: { kind: "", source: "", corporate_action_policy: "" },
      };
    }

    function defaultWindows(workflow) {
      return (workflow === "moving_average_comparison" ? ["train", "test"] : ["train", "validation", "test"])
        .map(newWindow);
    }

    function defaultCandidate(type = "moving_average_regime") {
      const strategy = strategies.get(type) || options.strategies[0];
      const parameters = {};
      for (const [name, descriptor] of Object.entries(strategy.fields || {})) {
        if (Object.hasOwn(descriptor, "default")) parameters[name] = clone(descriptor.default);
        else parameters[name] = descriptor.type === "boolean" ? false : descriptor.type === "string_array" ? [] : "";
      }
      for (const [name, value] of Object.entries(strategy.hidden_defaults || {})) {
        if (!Object.hasOwn(parameters, name)) parameters[name] = clone(value);
      }
      return {
        key: state.nextRow++, render: 0, controls: null,
        name: "candidate", type: strategy.type, symbol: "", eligible: true, parameters,
      };
    }

    function syncWindows() {
      for (const row of state.windows) {
        if (!row.controls) continue;
        row.name = row.controls.name.input.value;
        row.role = row.controls.role.value;
        row.regime = row.controls.regime.input.value;
        row.validation_id = row.controls.validation.input.value;
        row.evidence.kind = row.controls.kind.value;
        row.evidence.source = row.controls.source.input.value;
        row.evidence.corporate_action_policy = row.controls.policy.input.value;
      }
    }

    function syncCandidates() {
      for (const row of state.candidates) {
        if (!row.controls) continue;
        row.name = row.controls.name.input.value;
        row.type = row.controls.type.value;
        row.symbol = row.controls.symbol.input.value;
        row.eligible = row.controls.eligible.checked;
        const strategy = strategies.get(row.type);
        for (const [name, field] of Object.entries(row.controls.parameters || {})) {
          row.parameters[name] = rawScalar(field, strategy?.fields?.[name]);
        }
      }
    }

    function move(array, index, direction) {
      const destination = index + direction;
      if (destination < 0 || destination >= array.length) return;
      [array[index], array[destination]] = [array[destination], array[index]];
    }

    async function loadChoices(row, area, offset = 0) {
      if (!ui.isCurrentView(request)) return;
      const sequence = ++state.chooserSequence;
      const revision = state.revision;
      const render = row.render;
      area.replaceChildren(ui.element("p", { class: "muted", text: "Loading registered validations." }));
      try {
        const params = new URLSearchParams({ offset: String(offset), limit: "25" });
        const payload = await ui.api(`/api/v1/artifacts?${params}`);
        if (!ui.isCurrentView(request) || sequence !== state.chooserSequence || revision !== state.revision
            || render !== row.render || !state.windows.includes(row)) return;
        const list = ui.element("div", { class: "research-form-section" });
        const items = Array.isArray(payload?.items) ? payload.items.filter((item) =>
          item?.kind === "dataset_validation_v1" && (item?.integrity === "verified" || item?.status?.integrity === "verified")
          && VALIDATION_ID.test(item?.id || "")) : [];
        if (!items.length) list.append(ui.element("p", { class: "muted", text: "No verified validations on this catalog page." }));
        for (const item of items) {
          const choose = ui.element("button", { type: "button", text: `Choose ${item.name || "Unnamed validation"}` });
          choose.addEventListener("click", () => {
            if (!state.windows.includes(row) || row.render !== render) return;
            row.controls.validation.input.value = item.id;
            row.validation_id = item.id;
            area.replaceChildren();
            invalidateDraft();
          });
          list.append(ui.element("p", {}, [
            ui.element("span", { text: `${item.name || "Unnamed validation"} (${item.id}) ` }), choose,
          ]));
        }
        const paging = ui.element("nav", { class: "pagination", "aria-label": "Validation catalog pages" });
        const previous = ui.element("button", { type: "button", text: "Previous validations" });
        const next = ui.element("button", { type: "button", text: "Next validations" });
        const pageOffset = Number.isInteger(payload?.offset) ? payload.offset : offset;
        const pageLimit = Number.isInteger(payload?.limit) && payload.limit > 0 ? payload.limit : 25;
        const total = Number.isInteger(payload?.total) && payload.total >= 0 ? payload.total : pageOffset + pageLimit;
        previous.disabled = pageOffset <= 0;
        next.disabled = pageOffset + pageLimit >= total;
        previous.addEventListener("click", () => loadChoices(row, area, Math.max(0, pageOffset - pageLimit)));
        next.addEventListener("click", () => loadChoices(row, area, pageOffset + pageLimit));
        paging.append(previous, next);
        area.replaceChildren(list, paging);
      } catch {
        if (ui.isCurrentView(request) && sequence === state.chooserSequence && revision === state.revision
            && render === row.render && state.windows.includes(row)) {
          area.replaceChildren(ui.element("p", { class: "research-form-error", text: "The validation catalog page is unavailable." }));
        }
      }
    }

    function renderWindows() {
      state.fields = new Map([...state.fields].filter(([path]) => !path.startsWith("configuration.windows[")));
      const metadata = workflows.get(workflowInput.value);
      const container = section("Ordered research windows", "configuration.windows");
      container.append(ui.element("p", { class: "muted", text:
        "Validation IDs identify registered captured data. Evidence kind, source authenticity, and corporate-action policy are your declarations; checksums do not verify those claims. Final-test inputs are protected on acceptance, and their prior untouched history remains unknown." }));
      state.windows.forEach((row, index) => {
        row.render += 1;
        const path = `configuration.windows[${index}]`;
        const card = section(`Window ${index + 1}`, path);
        const name = makeScalar(`${path}.name`, { type: "string", required: true }, { required: true, label: "Safe name" });
        name.input.value = row.name;
        const roleLabel = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Role" })]);
        const role = register(`${path}.role`, ui.element("select", { name: `${path}.role` }, [
          ui.element("option", { value: "train", text: "Train" }),
          ui.element("option", { value: "validation", text: "Validation" }),
          ui.element("option", { value: "test", text: "Final test" }),
        ]));
        role.value = row.role;
        roleLabel.append(role);
        const regime = makeScalar(`${path}.regime`, { type: "string", required: true }, { required: true });
        regime.input.value = row.regime;
        const validation = makeScalar(`${path}.validation_id`, { type: "string", required: true }, {
          required: true, label: "Validated dataset ID (32 lowercase hex characters)",
        });
        validation.input.value = row.validation_id;
        validation.input.setAttribute("pattern", "[0-9a-f]{32}");
        const choose = ui.element("button", { type: "button", text: "Choose validated dataset" });
        const chooser = ui.element("div", { "aria-live": "polite" });
        choose.addEventListener("click", () => loadChoices(row, chooser));
        const evidence = section("Evidence declaration", `${path}.evidence`);
        const kindLabel = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Kind" })]);
        const kind = register(`${path}.evidence.kind`, ui.element("select", { name: `${path}.evidence.kind`, required: "" }, [
          ui.element("option", { value: "", text: "Select evidence kind" }),
          ui.element("option", { value: "historical", text: "Historical" }),
          ui.element("option", { value: "synthetic", text: "Synthetic" }),
        ]));
        kind.value = row.evidence.kind;
        kindLabel.append(kind);
        const source = makeScalar(`${path}.evidence.source`, { type: "string", required: true }, { required: true });
        source.input.value = row.evidence.source;
        const policy = makeScalar(`${path}.evidence.corporate_action_policy`, { type: "string", required: true }, {
          required: true, label: "Corporate-action policy",
        });
        policy.input.value = row.evidence.corporate_action_policy;
        evidence.append(kindLabel, source.wrapper, policy.wrapper);
        const controls = ui.element("div", { class: "research-form-actions" });
        const up = ui.element("button", { type: "button", text: "Move window up" });
        const down = ui.element("button", { type: "button", text: "Move window down" });
        const remove = ui.element("button", { type: "button", text: "Remove window" });
        up.disabled = index === 0 || workflowInput.value === "moving_average_comparison";
        down.disabled = index === state.windows.length - 1 || workflowInput.value === "moving_average_comparison";
        remove.disabled = state.windows.length <= metadata.min_windows || workflowInput.value === "moving_average_comparison";
        up.addEventListener("click", () => { syncWindows(); move(state.windows, index, -1); invalidateDraft(); renderWindows(); });
        down.addEventListener("click", () => { syncWindows(); move(state.windows, index, 1); invalidateDraft(); renderWindows(); });
        remove.addEventListener("click", () => {
          syncWindows(); state.windows.splice(index, 1); row.render += 1; state.chooserSequence += 1;
          invalidateDraft(); renderWindows();
        });
        controls.append(up, down, remove);
        row.controls = { name, role, regime, validation, kind, source, policy };
        card.append(name.wrapper, roleLabel, regime.wrapper, validation.wrapper, choose, chooser, evidence, controls);
        container.append(card);
      });
      const add = ui.element("button", { type: "button", text: "Add window" });
      add.disabled = state.windows.length >= metadata.max_windows || workflowInput.value === "moving_average_comparison";
      add.addEventListener("click", () => {
        syncWindows();
        const testIndex = state.windows.findIndex((value) => value.role === "test");
        state.windows.splice(testIndex < 0 ? state.windows.length : testIndex, 0, newWindow("validation"));
        invalidateDraft(); renderWindows();
      });
      container.append(add);
      windowsHost.replaceChildren(container);
    }

    function renderCandidates() {
      state.fields = new Map([...state.fields].filter(([path]) => !path.startsWith("configuration.candidates[")));
      const metadata = workflows.get(workflowInput.value);
      const container = section("Ordered candidates", "configuration.candidates");
      state.candidates.forEach((row, index) => {
        row.render += 1;
        const path = `configuration.candidates[${index}]`;
        const card = section(`Candidate ${index + 1}`, path);
        const name = makeScalar(`${path}.name`, { type: "string", required: true }, { required: true, label: "Safe name" });
        name.input.value = row.name;
        const typeLabel = ui.element("label", { class: "field-label" }, [ui.element("span", { text: "Registered strategy type" })]);
        const type = register(`${path}.type`, ui.element("select", { name: `${path}.type` }));
        for (const strategy of options.strategies) {
          type.append(ui.element("option", { value: strategy.type, text: strategy.label || labelFor(strategy.type) }));
        }
        if (workflowInput.value === "moving_average_comparison") {
          row.type = "moving_average_regime";
          type.disabled = true;
        }
        type.value = row.type;
        typeLabel.append(type);
        const symbol = makeScalar(`${path}.symbol`, { type: "string", required: true }, { required: true });
        symbol.input.value = row.symbol;
        const eligibleLabel = ui.element("label", { class: "research-form-include" });
        const eligible = register(`${path}.eligible`, ui.element("input", { type: "checkbox", name: `${path}.eligible` }));
        eligible.checked = row.eligible;
        if (workflowInput.value === "moving_average_comparison") eligible.disabled = true;
        eligibleLabel.append(eligible, globalThis.document.createTextNode(" Eligible for selection"));
        const strategy = strategies.get(row.type);
        const parameters = {};
        const exposed = section("Strategy parameters", `${path}.parameters`);
        const advanced = section("Advanced strategy parameters", `${path}.parameters.advanced`);
        const exposedNames = new Set(strategy?.exposed_fields || []);
        for (const [fieldName, descriptor] of Object.entries(strategy?.fields || {})) {
          const field = makeScalar(`${path}.parameters.${fieldName}`, descriptor, {
            required: descriptor.required === true || !Object.hasOwn(descriptor, "default"),
          });
          if (Object.hasOwn(row.parameters, fieldName)) {
            if (descriptor.type === "boolean") field.input.checked = row.parameters[fieldName] === true;
            else if (descriptor.type === "string_array") field.input.value = Array.isArray(row.parameters[fieldName])
              ? row.parameters[fieldName].join("\n") : "";
            else field.input.value = String(row.parameters[fieldName]);
          }
          parameters[fieldName] = field;
          (exposedNames.has(fieldName) ? exposed : advanced).append(field.wrapper);
        }
        const hidden = Object.entries(strategy?.hidden_defaults || {}).filter(([fieldName]) =>
          !Object.hasOwn(strategy?.fields || {}, fieldName));
        if (hidden.length) advanced.append(ui.element("p", { class: "muted", text:
          "Additional registry defaults are fixed by the server for this strategy." }));
        const details = ui.element("details", {}, [
          ui.element("summary", { text: "Advanced parameter defaults" }), advanced,
        ]);
        const controls = ui.element("div", { class: "research-form-actions" });
        const up = ui.element("button", { type: "button", text: "Move candidate up" });
        const down = ui.element("button", { type: "button", text: "Move candidate down" });
        const remove = ui.element("button", { type: "button", text: "Remove candidate" });
        up.disabled = index === 0 || workflowInput.value === "moving_average_comparison";
        down.disabled = index === state.candidates.length - 1 || workflowInput.value === "moving_average_comparison";
        remove.disabled = state.candidates.length <= 1 || workflowInput.value === "moving_average_comparison";
        up.addEventListener("click", () => { syncCandidates(); move(state.candidates, index, -1); invalidateDraft(); renderCandidates(); });
        down.addEventListener("click", () => { syncCandidates(); move(state.candidates, index, 1); invalidateDraft(); renderCandidates(); });
        remove.addEventListener("click", () => { syncCandidates(); state.candidates.splice(index, 1); invalidateDraft(); renderCandidates(); });
        controls.append(up, down, remove);
        type.addEventListener("change", () => {
          syncCandidates();
          const replacement = defaultCandidate(type.value);
          row.type = replacement.type;
          row.parameters = replacement.parameters;
          invalidateDraft(); renderCandidates();
        });
        row.controls = { name, type, symbol, eligible, parameters };
        card.append(name.wrapper, typeLabel, symbol.wrapper, eligibleLabel, exposed);
        if ([...Object.keys(strategy?.fields || {})].some((fieldName) => !exposedNames.has(fieldName)) || hidden.length) {
          card.append(details);
        }
        card.append(controls);
        container.append(card);
      });
      const add = ui.element("button", { type: "button", text: "Add candidate" });
      add.disabled = state.candidates.length >= metadata.max_candidates || workflowInput.value === "moving_average_comparison";
      add.addEventListener("click", () => { syncCandidates(); state.candidates.push(defaultCandidate()); invalidateDraft(); renderCandidates(); });
      container.append(add);
      candidatesHost.replaceChildren(container);
    }

    let experimentFields = {};
    function renderExperiment() {
      state.fields = new Map([...state.fields].filter(([path]) => !path.startsWith("configuration.experiment.")));
      const metadata = workflows.get(workflowInput.value);
      const container = section("Experiment and cost assumptions", "configuration.experiment");
      const core = section("Core assumptions", "configuration.experiment.core");
      const baseCosts = section("Base costs", "configuration.experiment.base_costs");
      const stressedCosts = section("Stressed costs", "configuration.experiment.stressed_costs");
      const advanced = section("Advanced assumptions", "configuration.experiment.advanced");
      experimentFields = {};
      let hasBaseCosts = false;
      let hasStressedCosts = false;
      let hasAdvanced = false;
      for (const [dotpath, descriptor] of Object.entries(metadata.experiment_fields || {})) {
        const field = makeScalar(`configuration.experiment.${dotpath}`, descriptor, {
          required: descriptor.required === true || Object.hasOwn(descriptor, "default"),
        });
        experimentFields[dotpath] = field;
        const top = dotpath.split(".")[0];
        if (top === "base_costs") { baseCosts.append(field.wrapper); hasBaseCosts = true; }
        else if (top === "stressed_costs") { stressedCosts.append(field.wrapper); hasStressedCosts = true; }
        else if (top === "initial_cash") core.append(field.wrapper);
        else { advanced.append(field.wrapper); hasAdvanced = true; }
      }
      container.append(core);
      if (hasBaseCosts) container.append(baseCosts);
      if (hasStressedCosts) container.append(stressedCosts);
      if (hasAdvanced) container.append(ui.element("details", {}, [
        ui.element("summary", { text: "Advanced experiment defaults" }), advanced,
      ]));
      experimentHost.replaceChildren(container);
    }

    function setNested(target, dotpath, value) {
      const names = dotpath.split(".");
      let current = target;
      for (const name of names.slice(0, -1)) {
        if (!Object.hasOwn(current, name)) current[name] = {};
        current = current[name];
      }
      current[names.at(-1)] = value;
    }

    function readWindow(row, index) {
      const path = `configuration.windows[${index}]`;
      const value = {
        name: row.controls.name.read().value,
        role: row.controls.role.value,
        regime: row.controls.regime.read().value,
        validation_id: row.controls.validation.read().value,
        evidence: {
          kind: row.controls.kind.value,
          source: row.controls.source.read().value,
          corporate_action_policy: row.controls.policy.read().value,
        },
      };
      if (!IDENTIFIER.test(value.name)) throw new DraftError(`${path}.name`, "Use a 1-64 character safe ASCII identifier.");
      if (!IDENTIFIER.test(value.regime)) throw new DraftError(`${path}.regime`, "Use a 1-64 character safe ASCII identifier.");
      if (!VALIDATION_ID.test(value.validation_id)) throw new DraftError(`${path}.validation_id`, "Use a 32-character lowercase hexadecimal validation ID.");
      if (!value.evidence.kind) throw new DraftError(`${path}.evidence.kind`, "Select historical or synthetic evidence.");
      return value;
    }

    function readCandidate(row, index) {
      const path = `configuration.candidates[${index}]`;
      const strategy = strategies.get(row.controls.type.value);
      if (!strategy) throw new DraftError(`${path}.type`, "Select a supported registered strategy.");
      const parameters = clone(strategy.hidden_defaults || {});
      for (const [name, field] of Object.entries(row.controls.parameters)) {
        const value = field.read();
        if (value.present) parameters[name] = value.value;
      }
      const candidate = {
        name: row.controls.name.read().value,
        type: row.controls.type.value,
        symbol: row.controls.symbol.read().value,
        parameters,
        eligible: row.controls.eligible.checked,
      };
      if (!IDENTIFIER.test(candidate.name)) throw new DraftError(`${path}.name`, "Use a 1-64 character safe ASCII identifier.");
      if (!candidate.symbol || candidate.symbol.length > 64 || candidate.symbol.trim() !== candidate.symbol) {
        throw new DraftError(`${path}.symbol`, "Use a nonempty symbol of at most 64 characters without surrounding whitespace.");
      }
      return candidate;
    }

    function buildConfiguration() {
      const workflow = workflowInput.value;
      const metadata = workflows.get(workflow);
      if (!metadata) throw new DraftError("configuration.workflow", "Select a supported workflow.");
      const windows = state.windows.map(readWindow);
      const candidates = state.candidates.map(readCandidate);
      if (windows.length < metadata.min_windows || windows.length > metadata.max_windows) {
        throw new DraftError("configuration.windows", `Use ${metadata.min_windows}-${metadata.max_windows} windows.`);
      }
      if (candidates.length < 1 || candidates.length > metadata.max_candidates) {
        throw new DraftError("configuration.candidates", `Use 1-${metadata.max_candidates} candidates.`);
      }
      if (new Set(windows.map((value) => value.name)).size !== windows.length) {
        throw new DraftError("configuration.windows", "Window names must be unique.");
      }
      if (new Set(windows.map((value) => value.validation_id)).size !== windows.length) {
        throw new DraftError("configuration.windows", "Each window must use a distinct validation ID.");
      }
      if (new Set(candidates.map((value) => value.name)).size !== candidates.length) {
        throw new DraftError("configuration.candidates", "Candidate names must be unique.");
      }
      const roles = windows.map((value) => value.role);
      if (workflow === "moving_average_comparison") {
        if (roles.join(",") !== "train,test") throw new DraftError("configuration.windows", "This workflow requires exactly train then final test.");
        if (candidates.length !== 1 || candidates[0].type !== "moving_average_regime" || !candidates[0].eligible) {
          throw new DraftError("configuration.candidates", "This workflow requires one eligible moving-average candidate.");
        }
      } else {
        const order = { train: 0, validation: 1, test: 2 };
        if (!roles.includes("train") || !roles.includes("validation") || roles.filter((value) => value === "test").length !== 1
            || roles.at(-1) !== "test" || roles.some((value, index) => index && order[value] < order[roles[index - 1]])) {
          throw new DraftError("configuration.windows", "Order train, then validation, then exactly one final-test window.");
        }
      }
      const experiment = {};
      for (const [dotpath, field] of Object.entries(experimentFields)) {
        const value = field.read();
        if (value.present) setNested(experiment, dotpath, value.value);
      }
      return { schema_version: 1, workflow, windows, candidates, experiment };
    }

    function envelope(configuration) {
      return { schema_version: 1, kind: "comparison_run", configuration: clone(configuration) };
    }

    function renderPreview(result) {
      const assumptions = ui.element("ol", { class: "research-form-assumptions" });
      for (const value of Array.isArray(result.assumptions) ? result.assumptions : []) {
        assumptions.append(ui.element("li", { text: String(value) }));
      }
      previewArea.replaceChildren(
        ui.element("h2", { text: "Previewed assumptions" }), assumptions,
        ui.element("p", { text: "Holdout history: unknown." }),
        ui.element("p", { class: "muted", text:
          "Preview does not reserve data, verify source authenticity or corporate-action claims, establish an untouched holdout, or execute a strategy. Launch acceptance protects final-test identities." }),
      );
    }

    async function preview() {
      if (!ui.isCurrentView(request) || state.previewing || state.submitting || state.uncertain) return;
      clearError();
      let configuration;
      try { configuration = buildConfiguration(); }
      catch (problem) { showError(problem, problem instanceof DraftError ? problem.message : "Complete the form before previewing."); return; }
      const revision = state.revision;
      state.preview = null;
      state.intent = null;
      previewArea.replaceChildren();
      launchButton.disabled = true;
      state.previewing = true;
      previewButton.disabled = true;
      ui.setStatus("Validating comparison inputs and assumptions.");
      try {
        const result = await ui.api("/api/v1/comparisons/preview", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(envelope(configuration)),
        });
        if (!ui.isCurrentView(request) || revision !== state.revision) return;
        if (!/^[0-9a-f]{64}$/.test(result?.preview_fingerprint || "")) {
          throw new Error("Incomplete preview response");
        }
        state.preview = freeze({ revision, configuration: clone(configuration), result: clone(result) });
        renderPreview(result);
        launchButton.disabled = false;
        ui.setStatus("");
      } catch (problem) {
        if (ui.isCurrentView(request) && revision === state.revision) {
          state.preview = null;
          launchButton.disabled = true;
          ui.setStatus("");
          showError(problem, "The comparison preview was not accepted.");
        }
      } finally {
        state.previewing = false;
        if (ui.isCurrentView(request) && revision !== state.revision && !state.submitting) ui.setStatus("");
        if (ui.isCurrentView(request) && !state.submitting && !state.uncertain) previewButton.disabled = false;
      }
    }

    function lockDraft(value) {
      draft.disabled = value;
      launchButton.disabled = value || !state.preview;
      previewButton.disabled = value || state.previewing;
    }

    function newIntent() {
      const value = timeoutField.read().value;
      const timeout = Number(value);
      if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 1800) {
        throw new DraftError("timeout_seconds", "Use an integer from 1 through 1800.");
      }
      return freeze({
        ...envelope(state.preview.configuration),
        preview_fingerprint: state.preview.result.preview_fingerprint,
        idempotency_key: secureKey(), timeout_seconds: timeout,
      });
    }

    async function launch() {
      if (!ui.isCurrentView(request) || state.previewing || state.submitting || !state.preview && !state.intent) return;
      clearError();
      if (!state.intent) {
        try { state.intent = newIntent(); }
        catch (problem) { showError(problem, problem instanceof DraftError ? problem.message : "The fixed request could not be created."); return; }
      }
      state.submitting = true;
      lockDraft(true);
      retryButton.hidden = true;
      reviewButton.hidden = true;
      newButton.hidden = true;
      ui.setStatus("Submitting the fixed comparison job.");
      try {
        const result = await ui.api("/api/v1/comparisons/jobs", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.intent),
        });
        if (!ui.isCurrentView(request)) return;
        const id = result?.job?.id;
        if (typeof id !== "string" || !id) throw new Error("Incomplete job response");
        ui.navigate(`/research/jobs/${encodeURIComponent(id)}`);
      } catch (problem) {
        if (!ui.isCurrentView(request)) return;
        ui.setStatus("");
        const definitiveRejection = Number.isInteger(problem?.status) && problem.status >= 400 && problem.status < 500;
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
            ? "This retry was rejected, but the earlier submission may already exist. Review recent jobs before starting a new comparison, which could create a duplicate."
            : "Submission outcome is unknown. A job may already exist. Retry sends the exact same frozen request and idempotency key; starting a new comparison could create a duplicate.";
          error.hidden = false;
          error.focus({ preventScroll: true });
        }
      } finally {
        state.submitting = false;
      }
    }

    function resetWorkflow() {
      state.chooserSequence += 1;
      state.windows = defaultWindows(workflowInput.value);
      state.candidates = [defaultCandidate("moving_average_regime")];
      renderWindows();
      renderCandidates();
      renderExperiment();
    }

    workflowInput.addEventListener("change", () => { invalidateDraft(); resetWorkflow(); });
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
      ui.setStatus("Draft retained. Preview again before starting a new comparison.");
    });

    register("configuration.workflow", workflowInput);
    resetWorkflow();
    ui.replaceView(
      ui.element("p", { class: "muted" }, [ui.textLink("/research", "Research")]),
      ui.element("h1", { text: "New comparison" }),
      ui.element("p", { class: "lede", text:
        "Configure a fixed offline comparison from registered validations. No strategy runs until explicit preview and launch are complete." }),
      form,
    );
    ui.setStatus("");
  }

  async function load() {
    const request = ui.beginView();
    ui.setNavigation("research");
    ui.setStatus("Loading comparison form metadata.");
    try {
      const options = validateOptions(await ui.api("/api/v1/comparisons/form-options"));
      if (!ui.isCurrentView(request)) return;
      createController(options, request);
    } catch {
      if (!ui.isCurrentView(request)) return;
      ui.setStatus("");
      ui.replaceView(
        ui.element("h1", { text: "Comparison form unavailable" }),
        ui.element("p", { class: "error", text: "Comparison form metadata could not be loaded." }),
        ui.textLink("/research", "Return to Research"),
      );
    }
  }

  return { load };
}
