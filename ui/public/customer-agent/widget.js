// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
/*
 * The bank's customer agent as an embeddable element.
 *
 * The bank's page loads this script and places
 *   <agenticorg-customer-agent api-base="https://api.example-bank.test/api/v1"
 *                              session-token="cas_..."></agenticorg-customer-agent>
 * after it opened a customer session from its own backend with a signed
 * assertion. The element never sees a bank credential: it holds only the
 * opaque session token the host injects, talks to the customer-agent routes,
 * and renders answers, prepared-action cards with one-tap confirm and cancel,
 * the "why this" panel, and the AI disclosure that must be acknowledged
 * before the first turn.
 *
 * Step-ups stay with the bank: when an action needs the bank's approval the
 * element fires "customer-agent:approval-required" with the action id; the
 * host runs its own passkey, biometric or OTP flow and calls
 * element.approve(actionId, { method, assertion }).
 *
 * No dependencies, no inline HTML from server data: every string is set with
 * textContent, so nothing the agent or the customer typed is interpreted as
 * markup.
 */
(function (root) {
  "use strict";

  var STRINGS = {
    title: "Your bank assistant",
    placeholder: "Ask about your accounts, or tell me what to do",
    send: "Send",
    person: "Talk to a person",
    permissions: "Permissions",
    acknowledge: "I understand",
    confirm: "Confirm",
    cancel: "Cancel",
    why: "Why this?",
    approval: "Approve with the bank's check",
    thinking: "One moment",
    offline: "The assistant could not be reached. Please try again.",
    expired: "Your session has ended. Please sign in to your bank again.",
  };

  function createClient(apiBase, token, fetchImpl) {
    var doFetch = fetchImpl || root.fetch.bind(root);
    var base = String(apiBase || "").replace(/\/+$/, "");

    function call(method, path, body) {
      var init = {
        method: method,
        headers: { Authorization: "Bearer " + token, Accept: "application/json" },
      };
      if (body !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(body);
      }
      return doFetch(base + "/customer-agent" + path, init).then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!response.ok) {
              var error = new Error((data && data.detail && (data.detail.message || data.detail)) || response.statusText);
              error.status = response.status;
              error.detail = data && data.detail;
              throw error;
            }
            return data;
          });
      });
    }

    return {
      session: function () {
        return call("GET", "/session");
      },
      acknowledge: function () {
        return call("POST", "/session/acknowledge");
      },
      turn: function (text, approval) {
        var body = { text: text };
        if (approval) body.approval = approval;
        return call("POST", "/turns", body);
      },
      confirm: function (actionId, approval) {
        return call("POST", "/actions/" + encodeURIComponent(actionId) + "/confirm", approval ? { approval: approval } : {});
      },
      cancel: function (actionId) {
        return call("POST", "/actions/" + encodeURIComponent(actionId) + "/cancel", {});
      },
      actions: function () {
        return call("GET", "/actions");
      },
      permissions: function () {
        return call("GET", "/permissions");
      },
      setMandate: function (mandate) {
        return call("PUT", "/permissions/mandates", mandate);
      },
      revokeMandate: function (mandateId) {
        return call("DELETE", "/permissions/mandates/" + encodeURIComponent(mandateId));
      },
      end: function () {
        return call("DELETE", "/session");
      },
    };
  }

  var STYLE =
    ":host{all:initial;display:block;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1b1f23}" +
    ".shell{display:flex;flex-direction:column;height:100%;min-height:420px;max-height:720px;border:1px solid #d7dde3;border-radius:14px;background:#fff;overflow:hidden}" +
    "header{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-bottom:1px solid #e5e9ed;background:#f7f9fb}" +
    "header h1{font-size:15px;margin:0;font-weight:600}" +
    "header .links button{background:none;border:0;color:#0b57d0;cursor:pointer;font:inherit;margin-left:10px;padding:0}" +
    ".log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}" +
    ".msg{max-width:88%;padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}" +
    ".msg.user{align-self:flex-end;background:#0b57d0;color:#fff;border-bottom-right-radius:4px}" +
    ".msg.agent{align-self:flex-start;background:#eef2f6;border-bottom-left-radius:4px}" +
    ".msg.system{align-self:center;background:transparent;color:#5f6b76;font-size:12px}" +
    ".card{align-self:flex-start;width:88%;border:1px solid #cfd8e3;border-radius:12px;padding:12px;background:#fff}" +
    ".card h2{font-size:14px;margin:0 0 4px}" +
    ".card .summary{margin:0 0 8px;color:#333}" +
    ".card .params{margin:0 0 8px;padding:0;list-style:none;color:#5f6b76;font-size:12px}" +
    ".card .actions{display:flex;gap:8px;flex-wrap:wrap}" +
    ".card button{font:inherit;border-radius:8px;padding:7px 12px;cursor:pointer;border:1px solid #0b57d0;background:#0b57d0;color:#fff}" +
    ".card button.secondary{background:#fff;color:#0b57d0}" +
    ".card button.plain{background:none;border:0;color:#0b57d0;padding:7px 4px}" +
    ".card .state{font-size:12px;color:#5f6b76;margin-top:6px}" +
    ".why{margin-top:8px;border-top:1px dashed #d7dde3;padding-top:8px;font-size:12px;color:#333}" +
    ".why dt{font-weight:600;margin-top:6px}.why dd{margin:0}" +
    ".disclosure{padding:16px;background:#fff8e6;border-top:1px solid #f1dd9a}" +
    ".disclosure p{margin:0 0 10px}" +
    ".disclosure button,form button{font:inherit;border-radius:8px;padding:8px 14px;cursor:pointer;border:1px solid #0b57d0;background:#0b57d0;color:#fff}" +
    "form{display:flex;gap:8px;padding:12px;border-top:1px solid #e5e9ed}" +
    "form input{flex:1;font:inherit;padding:9px 12px;border:1px solid #c9d2db;border-radius:8px}" +
    "form input:focus{outline:2px solid #0b57d0;outline-offset:1px}" +
    ".hidden{display:none}";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function formatValue(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  var CustomerAgentElement = null;

  if (root.HTMLElement && root.customElements) {
    CustomerAgentElement = function () {
      var self = Reflect.construct(HTMLElement, [], CustomerAgentElement);
      self._client = null;
      self._disclosureAcknowledged = false;
      self._busy = false;
      self._cards = {};
      return self;
    };
    CustomerAgentElement.prototype = Object.create(HTMLElement.prototype);
    CustomerAgentElement.prototype.constructor = CustomerAgentElement;
    Object.setPrototypeOf(CustomerAgentElement, HTMLElement);

    Object.defineProperty(CustomerAgentElement, "observedAttributes", {
      get: function () {
        return ["api-base", "session-token"];
      },
    });

    CustomerAgentElement.prototype.connectedCallback = function () {
      if (!this.shadowRoot) this._render();
      this._connect();
    };

    CustomerAgentElement.prototype.attributeChangedCallback = function () {
      if (this.isConnected && this.shadowRoot) this._connect();
    };

    CustomerAgentElement.prototype._render = function () {
      var shadow = this.attachShadow({ mode: "open" });
      var style = el("style", null, STYLE);
      var shell = el("div", "shell");
      var header = el("header");
      header.appendChild(el("h1", null, STRINGS.title));
      var links = el("div", "links");
      var person = el("button", null, STRINGS.person);
      person.type = "button";
      person.setAttribute("data-role", "person");
      var permissions = el("button", null, STRINGS.permissions);
      permissions.type = "button";
      permissions.setAttribute("data-role", "permissions");
      links.appendChild(person);
      links.appendChild(permissions);
      header.appendChild(links);
      var log = el("div", "log");
      log.setAttribute("role", "log");
      log.setAttribute("aria-live", "polite");
      var disclosure = el("div", "disclosure hidden");
      disclosure.setAttribute("data-role", "disclosure");
      disclosure.appendChild(el("p", null, ""));
      var ack = el("button", null, STRINGS.acknowledge);
      ack.type = "button";
      ack.setAttribute("data-role", "acknowledge");
      disclosure.appendChild(ack);
      var form = el("form");
      var input = el("input");
      input.type = "text";
      input.maxLength = 2000;
      input.placeholder = STRINGS.placeholder;
      input.setAttribute("aria-label", STRINGS.placeholder);
      input.autocomplete = "off";
      var send = el("button", null, STRINGS.send);
      send.type = "submit";
      form.appendChild(input);
      form.appendChild(send);
      shell.appendChild(header);
      shell.appendChild(log);
      shell.appendChild(disclosure);
      shell.appendChild(form);
      shadow.appendChild(style);
      shadow.appendChild(shell);

      var that = this;
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var text = input.value.trim();
        if (!text) return;
        input.value = "";
        that.send(text);
      });
      ack.addEventListener("click", function () {
        that._acknowledge();
      });
      person.addEventListener("click", function () {
        that.send("I want to talk to a person");
      });
      permissions.addEventListener("click", function () {
        that.dispatchEvent(new CustomEvent("customer-agent:permissions", { bubbles: true, composed: true }));
      });
      this._nodes = { log: log, disclosure: disclosure, input: input, send: send, form: form };
    };

    CustomerAgentElement.prototype._connect = function () {
      var apiBase = this.getAttribute("api-base") || "";
      var token = this.getAttribute("session-token") || "";
      if (!apiBase || !token) return;
      this._client = createClient(apiBase, token, this.fetchImpl);
      var that = this;
      this._client
        .session()
        .then(function (session) {
          that._disclosureAcknowledged = !!session.disclosure_acknowledged;
          that._nodes.disclosure.querySelector("p").textContent = session.disclosure || "";
          that._nodes.disclosure.classList.toggle("hidden", that._disclosureAcknowledged);
          that._nodes.form.classList.toggle("hidden", !that._disclosureAcknowledged);
          that.dispatchEvent(new CustomEvent("customer-agent:ready", { detail: session, bubbles: true, composed: true }));
        })
        .catch(function (error) {
          that._system(error && error.status === 401 ? STRINGS.expired : STRINGS.offline);
        });
    };

    CustomerAgentElement.prototype._acknowledge = function () {
      var that = this;
      if (!this._client) return;
      this._client
        .acknowledge()
        .then(function () {
          that._disclosureAcknowledged = true;
          that._nodes.disclosure.classList.add("hidden");
          that._nodes.form.classList.remove("hidden");
          that._nodes.input.focus();
        })
        .catch(function () {
          that._system(STRINGS.offline);
        });
    };

    CustomerAgentElement.prototype._append = function (node) {
      this._nodes.log.appendChild(node);
      this._nodes.log.scrollTop = this._nodes.log.scrollHeight;
      return node;
    };

    CustomerAgentElement.prototype._system = function (text) {
      this._append(el("div", "msg system", text));
    };

    CustomerAgentElement.prototype._setBusy = function (busy) {
      this._busy = busy;
      this._nodes.send.disabled = busy;
      this._nodes.input.disabled = busy;
    };

    CustomerAgentElement.prototype.send = function (text, approval) {
      if (!this._client || this._busy || !this._disclosureAcknowledged) return Promise.resolve(null);
      var that = this;
      this._append(el("div", "msg user", text));
      this._setBusy(true);
      return this._client
        .turn(text, approval)
        .then(function (turn) {
          that._renderTurn(turn);
          return turn;
        })
        .catch(function (error) {
          that._system(error && error.status === 401 ? STRINGS.expired : STRINGS.offline);
          return null;
        })
        .then(function (turn) {
          that._setBusy(false);
          return turn;
        });
    };

    CustomerAgentElement.prototype.approve = function (actionId, approval) {
      var that = this;
      if (!this._client) return Promise.resolve(null);
      this._setBusy(true);
      return this._client
        .confirm(actionId, approval)
        .then(function (turn) {
          that._renderTurn(turn);
          return turn;
        })
        .catch(function (error) {
          that._system((error && error.message) || STRINGS.offline);
          return null;
        })
        .then(function (turn) {
          that._setBusy(false);
          return turn;
        });
    };

    CustomerAgentElement.prototype._renderTurn = function (turn) {
      if (!turn) return;
      if (turn.answer) this._append(el("div", "msg agent", turn.answer));
      if (turn.card) this._renderCard(turn.card, turn.explanation, turn.approval_required);
      else if (turn.explanation && turn.outcome && turn.outcome.kind === "execute") {
        this._append(this._whyPanel(turn.explanation, true));
      }
      this.dispatchEvent(new CustomEvent("customer-agent:turn", { detail: turn, bubbles: true, composed: true }));
    };

    CustomerAgentElement.prototype._whyPanel = function (explanation, open) {
      var details = el("details", "why");
      if (open) details.open = true;
      details.appendChild(el("summary", null, STRINGS.why));
      var list = el("dl");
      function row(label, value) {
        if (value === undefined || value === null || value === "") return;
        list.appendChild(el("dt", null, label));
        list.appendChild(el("dd", null, formatValue(value)));
      }
      row("What was used", Object.keys(explanation.data_used || {}).length ? explanation.data_used : null);
      row("The rule applied", explanation.rule);
      row("Your mandate", explanation.mandate);
      row("Alternatives", (explanation.alternatives || []).join("; "));
      row("What the bank earns", explanation.bank_earns);
      row("How to challenge this", explanation.challenge);
      row("", explanation.ai_disclosure);
      details.appendChild(list);
      return details;
    };

    CustomerAgentElement.prototype._renderCard = function (card, explanation, approvalRequired) {
      var that = this;
      var existing = this._cards[card.action_id];
      var node = existing || el("div", "card");
      node.textContent = "";
      node.setAttribute("data-action-id", card.action_id);
      node.appendChild(el("h2", null, card.title));
      if (card.summary) node.appendChild(el("p", "summary", card.summary));
      var params = el("ul", "params");
      Object.keys(card.parameters || {}).forEach(function (key) {
        params.appendChild(el("li", null, key + ": " + formatValue(card.parameters[key])));
      });
      if (params.childNodes.length) node.appendChild(params);
      var actions = el("div", "actions");
      var terminal = card.status && card.status !== "prepared";
      if (!terminal) {
        if (card.requires === "step_up" || approvalRequired) {
          var approve = el("button", null, STRINGS.approval);
          approve.type = "button";
          approve.addEventListener("click", function () {
            that.dispatchEvent(
              new CustomEvent("customer-agent:approval-required", {
                detail: { action_id: card.action_id, card: card },
                bubbles: true,
                composed: true,
              })
            );
          });
          actions.appendChild(approve);
        } else if (card.requires === "confirm") {
          var confirm = el("button", null, STRINGS.confirm);
          confirm.type = "button";
          confirm.addEventListener("click", function () {
            that._decide(card.action_id, true);
          });
          actions.appendChild(confirm);
        }
        var cancel = el("button", "secondary", STRINGS.cancel);
        cancel.type = "button";
        cancel.addEventListener("click", function () {
          that._decide(card.action_id, false);
        });
        actions.appendChild(cancel);
        node.appendChild(actions);
      } else {
        node.appendChild(el("div", "state", card.status));
      }
      if (explanation) node.appendChild(this._whyPanel(explanation, false));
      if (!existing) {
        this._cards[card.action_id] = node;
        this._append(node);
      }
    };

    CustomerAgentElement.prototype._decide = function (actionId, confirmed) {
      var that = this;
      if (!this._client || this._busy) return;
      this._setBusy(true);
      var call = confirmed ? this._client.confirm(actionId) : this._client.cancel(actionId);
      call
        .then(function (turn) {
          that._renderTurn(turn);
        })
        .catch(function (error) {
          that._system((error && error.message) || STRINGS.offline);
        })
        .then(function () {
          that._setBusy(false);
        });
    };

    if (!root.customElements.get("agenticorg-customer-agent")) {
      root.customElements.define("agenticorg-customer-agent", CustomerAgentElement);
    }
  }

  root.AgenticOrgCustomerAgent = {
    createClient: createClient,
    Element: CustomerAgentElement,
    strings: STRINGS,
    version: "0.1.0",
  };
})(typeof window !== "undefined" ? window : globalThis);
