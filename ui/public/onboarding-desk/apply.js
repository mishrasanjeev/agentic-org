// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/*
 * The merchant onboarding desk's applicant page as an embeddable element.
 *
 * A partner's page loads this script and places
 *   <agenticorg-onboarding api-base="https://app.agenticorg.ai/api/v1"></agenticorg-onboarding>
 * where its onboarding link lands. The element reads the signed link token from the page's
 * "t" query parameter (or a link-token attribute), removes it from the address bar, exchanges it
 * for a two-hour applicant session and shows the application in the applicant's language,
 * English or French, in the partner's colour: every item with why it is needed, answers saved
 * as the applicant goes, documents with the values they suggest for the applicant to confirm,
 * "talk to a person", and submit once every required item is in.
 *
 * No dependencies, no inline HTML from server data: every string is set with textContent, so
 * nothing the applicant typed or a document contained is interpreted as markup. The session
 * lives in memory only, never in storage.
 */
(function (root) {
  "use strict";

  var STRINGS = {
    en: {
      title: "Apply with {partner}",
      intro:
        "An AI assistant helps with this application. You can ask for a person at any time. Your answers are saved as you go.",
      privacy: "Privacy notice",
      progress: "{done} of {total} required items done",
      required: "Required",
      optional: "Optional",
      save: "Save",
      saved: "Saved",
      choose: "Choose one",
      street: "Street address",
      street2: "Apartment, suite or unit (optional)",
      city: "City",
      region: "State or province (two letters)",
      postal: "ZIP or postal code",
      ownerName: "Full name",
      ownerKind: "Person or business",
      person: "Person",
      business: "Business",
      ownerRole: "Role",
      owner: "Owner",
      controller: "Person in control",
      both: "Owner and in control",
      ownership: "Ownership % (optional)",
      dob: "Date of birth, YYYY-MM-DD (optional)",
      addOwner: "Add another person or business",
      remove: "Remove",
      upload: "Upload",
      chooseFile: "PDF, PNG or JPEG, up to 8 MB",
      uploaded: "Uploaded",
      suggestions: "We found these in your document. Use them?",
      useSelected: "Use selected",
      nothingFound: "We couldn't read any details from this file. Please type them in.",
      talk: "Talk to a person",
      talkPrompt: "What would you like help with? (optional)",
      send: "Send",
      talkDone: "Thanks. A person will contact you soon.",
      submit: "Submit application",
      submitNotReady: "Finish the required items to submit.",
      submitted: "Your application is submitted. {partner} will be in touch.",
      busy: "One moment",
      offline: "We couldn't reach the server. Please try again.",
      noLink: "Open the link from your message to start your application.",
      linkInvalid: "This link has expired. Ask {partner} for a new one.",
      sessionEnded: "Your session ended. Open the link from your message again.",
      closed: "This application is no longer open.",
    },
    fr: {
      title: "Demande auprès de {partner}",
      intro:
        "Un assistant IA vous aide avec cette demande. Vous pouvez demander une personne à tout moment. Vos réponses sont enregistrées au fur et à mesure.",
      privacy: "Avis de confidentialité",
      progress: "{done} sur {total} éléments obligatoires remplis",
      required: "Obligatoire",
      optional: "Facultatif",
      save: "Enregistrer",
      saved: "Enregistré",
      choose: "Choisissez",
      street: "Adresse",
      street2: "Appartement, bureau ou unité (facultatif)",
      city: "Ville",
      region: "Province ou État (deux lettres)",
      postal: "Code postal",
      ownerName: "Nom complet",
      ownerKind: "Personne ou entreprise",
      person: "Personne",
      business: "Entreprise",
      ownerRole: "Rôle",
      owner: "Propriétaire",
      controller: "Personne qui exerce le contrôle",
      both: "Propriétaire et contrôle",
      ownership: "Part en % (facultatif)",
      dob: "Date de naissance, AAAA-MM-JJ (facultatif)",
      addOwner: "Ajouter une personne ou une entreprise",
      remove: "Retirer",
      upload: "Téléverser",
      chooseFile: "PDF, PNG ou JPEG, jusqu'à 8 Mo",
      uploaded: "Téléversé",
      suggestions: "Nous avons trouvé ceci dans votre document. L'utiliser?",
      useSelected: "Utiliser la sélection",
      nothingFound: "Nous n'avons pu lire aucun détail dans ce fichier. Veuillez les saisir.",
      talk: "Parler à une personne",
      talkPrompt: "Avec quoi souhaitez-vous de l'aide? (facultatif)",
      send: "Envoyer",
      talkDone: "Merci. Une personne communiquera avec vous sous peu.",
      submit: "Soumettre la demande",
      submitNotReady: "Remplissez les éléments obligatoires pour soumettre.",
      submitted: "Votre demande est soumise. {partner} communiquera avec vous.",
      busy: "Un instant",
      offline: "Impossible de joindre le serveur. Veuillez réessayer.",
      noLink: "Ouvrez le lien de votre message pour commencer votre demande.",
      linkInvalid: "Ce lien a expiré. Demandez-en un nouveau à {partner}.",
      sessionEnded: "Votre session a pris fin. Ouvrez de nouveau le lien de votre message.",
      closed: "Cette demande n'est plus ouverte.",
    },
  };

  var COLOR_RE = /^#[0-9a-fA-F]{6}$/;

  function format(template, values) {
    return String(template).replace(/\{(\w+)\}/g, function (_, key) {
      return values && values[key] !== undefined ? String(values[key]) : "";
    });
  }

  function createClient(apiBase, fetchImpl) {
    var doFetch = fetchImpl || root.fetch.bind(root);
    var base = String(apiBase || "").replace(/\/+$/, "") + "/onboarding-desk/apply";
    var session = "";

    function call(method, path, body) {
      var init = { method: method, headers: { Accept: "application/json" }, credentials: "omit" };
      if (session) init.headers["X-Applicant-Session"] = session;
      if (body instanceof (root.FormData || function () {})) {
        init.body = body;
      } else if (body !== undefined) {
        init.headers["Content-Type"] = "application/json";
        init.body = JSON.stringify(body);
      }
      return doFetch(base + path, init).then(function (response) {
        return response
          .json()
          .catch(function () {
            return {};
          })
          .then(function (data) {
            if (!response.ok) {
              var detail = (data && data.detail) || {};
              var error = new Error(detail.message || response.statusText || "request_failed");
              error.status = response.status;
              error.code = detail.error || "";
              throw error;
            }
            return data;
          });
      });
    }

    return {
      open: function (linkToken) {
        return call("POST", "/session", { t: linkToken }).then(function (data) {
          session = data.session;
          return data;
        });
      },
      application: function () {
        return call("GET", "/application");
      },
      answers: function (answers) {
        return call("PUT", "/answers", { answers: answers });
      },
      upload: function (item, file) {
        var form = new root.FormData();
        form.append("item", item);
        form.append("file", file);
        return call("POST", "/documents", form);
      },
      confirm: function (documentId, items) {
        return call("POST", "/documents/" + encodeURIComponent(documentId) + "/confirm", { items: items });
      },
      remove: function (documentId) {
        return call("DELETE", "/documents/" + encodeURIComponent(documentId));
      },
      person: function (message) {
        return call("POST", "/person", { message: message || "" });
      },
      submit: function () {
        return call("POST", "/submit");
      },
      hasSession: function () {
        return Boolean(session);
      },
    };
  }

  function linkTokenFrom(location, history) {
    if (!location || !location.search) return "";
    var params = new URLSearchParams(location.search);
    var token = params.get("t") || "";
    if (token && history && history.replaceState) {
      params.delete("t");
      var query = params.toString();
      history.replaceState(history.state, "", location.pathname + (query ? "?" + query : "") + (location.hash || ""));
    }
    return token;
  }

  var STYLE =
    ":host{display:block;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#1f2933;--brand:#0b57d0}" +
    ".wrap{max-width:44rem;margin:0 auto;padding:16px}" +
    "h1{font-size:22px;margin:0 0 8px}" +
    ".intro{margin:0 0 6px;color:#3e4c59}" +
    ".privacy{font-size:13px;color:var(--brand)}" +
    ".progress{margin:12px 0;font-size:14px;color:#3e4c59}" +
    ".bar{height:6px;border-radius:3px;background:#e4e7eb;overflow:hidden}.bar span{display:block;height:100%;background:var(--brand)}" +
    ".item{border:1px solid #d9e2ec;border-radius:12px;padding:14px;margin:12px 0;background:#fff}" +
    ".item h2{font-size:16px;margin:0 0 4px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}" +
    ".tag{font-size:11px;font-weight:600;text-transform:uppercase;color:#52606d}" +
    ".tag.done{color:#0e7c3a}" +
    ".reason{margin:0 0 10px;font-size:13px;color:#52606d}" +
    "label{display:block;font-size:13px;margin:6px 0 2px;color:#3e4c59}" +
    "input,select,textarea{box-sizing:border-box;width:100%;font:inherit;padding:8px 10px;border:1px solid #bcccdc;border-radius:8px;background:#fff}" +
    "input:focus,select:focus,textarea:focus{outline:2px solid var(--brand);outline-offset:1px}" +
    "button{font:inherit;border-radius:8px;padding:8px 14px;cursor:pointer;border:1px solid var(--brand);background:var(--brand);color:#fff;margin-top:10px}" +
    "button.secondary{background:#fff;color:var(--brand)}" +
    "button:disabled{opacity:.5;cursor:not-allowed}" +
    ".row{display:grid;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));gap:8px}" +
    ".owner{border-top:1px dashed #d9e2ec;padding-top:8px;margin-top:8px}" +
    ".error{color:#b42318;font-size:13px;margin:6px 0 0}" +
    ".note{font-size:13px;color:#0e7c3a;margin:6px 0 0}" +
    ".docs{margin:8px 0 0;padding:0;list-style:none;font-size:14px}.docs li{display:flex;gap:8px;align-items:center;justify-content:space-between}" +
    ".docs button{margin:0;padding:4px 10px}" +
    ".suggest{background:#f0f4f8;border-radius:8px;padding:10px;margin-top:8px}" +
    ".suggest label{display:flex;gap:8px;align-items:center}.suggest input{width:auto}" +
    ".footer{border-top:1px solid #d9e2ec;margin-top:16px;padding-top:12px}" +
    ".message{padding:24px 0;font-size:16px}" +
    ".hidden{display:none}";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function field(labelText, input) {
    var wrap = el("div");
    var label = el("label", null, labelText);
    var id = "f" + Math.random().toString(36).slice(2);
    input.id = id;
    label.htmlFor = id;
    wrap.appendChild(label);
    wrap.appendChild(input);
    return wrap;
  }

  function input(value, attrs) {
    var node = el("input");
    node.value = value === null || value === undefined ? "" : String(value);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    return node;
  }

  function select(options, value, placeholder) {
    var node = el("select");
    if (placeholder) {
      var empty = el("option", null, placeholder);
      empty.value = "";
      node.appendChild(empty);
    }
    options.forEach(function (option) {
      var opt = el("option", null, option.label);
      opt.value = option.value;
      if (option.value === value) opt.selected = true;
      node.appendChild(opt);
    });
    return node;
  }

  var OnboardingElement = null;

  if (root.HTMLElement && root.customElements) {
    OnboardingElement = function () {
      var self = Reflect.construct(HTMLElement, [], OnboardingElement);
      self._client = null;
      self._app = null;
      self._partner = "";
      self._cards = {};
      self._snapshots = {};
      return self;
    };
    OnboardingElement.prototype = Object.create(HTMLElement.prototype);
    OnboardingElement.prototype.constructor = OnboardingElement;
    Object.setPrototypeOf(OnboardingElement, HTMLElement);

    OnboardingElement.prototype.connectedCallback = function () {
      if (this.shadowRoot) return;
      var shadow = this.attachShadow({ mode: "open" });
      var style = el("style");
      style.textContent = STYLE;
      shadow.appendChild(style);
      this._wrap = el("div", "wrap");
      shadow.appendChild(this._wrap);
      this._lang = (this.getAttribute("lang") || "").slice(0, 2) === "fr" ? "fr" : "en";
      this._message(this._t("busy"));
      this.start();
    };

    OnboardingElement.prototype._t = function (key, values) {
      return format((STRINGS[this._lang] || STRINGS.en)[key], values);
    };

    OnboardingElement.prototype._message = function (text) {
      this._wrap.textContent = "";
      this._wrap.appendChild(el("p", "message", text));
    };

    OnboardingElement.prototype._fail = function (error) {
      var partner = this._partner || "";
      if (error && error.code === "link_invalid") return this._message(this._t("linkInvalid", { partner: partner }));
      if (error && error.status === 401) return this._message(this._t("sessionEnded"));
      if (error && (error.status === 410 || error.code === "application_closed")) return this._message(this._t("closed"));
      if (error && error.status && error.message) return error.message;
      return this._t("offline");
    };

    OnboardingElement.prototype.start = function () {
      var self = this;
      var token = this.getAttribute("link-token") || linkTokenFrom(root.location, root.history);
      if (!token) return Promise.resolve(this._message(this._t("noLink")));
      this._client = createClient(this.getAttribute("api-base") || "/api/v1", this.fetchImpl);
      return this._client.open(token).then(
        function (data) {
          self._partner = data.partner_name || "";
          if (COLOR_RE.test(data.brand_color || "")) self.style.setProperty("--brand", data.brand_color);
          self._privacy = /^https:\/\//.test(data.privacy_url || "") ? data.privacy_url : "";
          self._render(data.application);
          self.dispatchEvent(new CustomEvent("onboarding:ready", { detail: { status: data.application.status } }));
        },
        function (error) {
          var text = self._fail(error);
          if (typeof text === "string") self._message(text);
        }
      );
    };

    OnboardingElement.prototype._render = function (application) {
      this._app = application;
      this._lang = application.locale === "fr" ? "fr" : "en";
      this._wrap.textContent = "";
      this._cards = {};
      this._snapshots = {};
      this._wrap.appendChild(el("h1", null, this._t("title", { partner: this._partner })));
      this._wrap.appendChild(el("p", "intro", this._t("intro")));
      if (this._privacy) {
        var link = el("a", "privacy", this._t("privacy"));
        link.href = this._privacy;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        this._wrap.appendChild(link);
      }
      if (application.status === "submitted") {
        this._wrap.appendChild(el("p", "message", this._t("submitted", { partner: this._partner })));
        return;
      }
      this._progress = el("div", "progress");
      this._wrap.appendChild(this._progress);
      this._list = el("div", "items");
      this._wrap.appendChild(this._list);
      var self = this;
      application.items.forEach(function (item) {
        var card = self._card(item);
        self._cards[item.id] = card;
        self._snapshots[item.id] = self._snapshot(item);
        self._list.appendChild(card);
      });
      this._wrap.appendChild(this._footer());
      this._update(application);
    };

    OnboardingElement.prototype._snapshot = function (item) {
      return JSON.stringify([item.answer, item.documents]);
    };

    OnboardingElement.prototype._update = function (application) {
      var self = this;
      this._app = application;
      if (application.status === "submitted") return this._render(application);
      application.items.forEach(function (item) {
        var snap = self._snapshot(item);
        if (self._snapshots[item.id] !== snap && self._cards[item.id]) {
          var fresh = self._card(item);
          self._list.replaceChild(fresh, self._cards[item.id]);
          self._cards[item.id] = fresh;
          self._snapshots[item.id] = snap;
        }
      });
      var required = application.items.filter(function (i) {
        return i.required;
      }).length;
      var done = required - application.missing.length;
      this._progress.textContent = "";
      this._progress.appendChild(el("div", null, this._t("progress", { done: done, total: required })));
      var bar = el("div", "bar");
      var fill = el("span");
      fill.style.width = (required ? Math.round((done / required) * 100) : 100) + "%";
      bar.appendChild(fill);
      this._progress.appendChild(bar);
      this._submit.disabled = !application.ready;
      this._submitHint.textContent = application.ready ? "" : this._t("submitNotReady");
      if (application.person_requested) this._talkNote.textContent = this._t("talkDone");
    };

    OnboardingElement.prototype._card = function (item) {
      var card = el("section", "item");
      card.setAttribute("data-item", item.id);
      var heading = el("h2", null, item.label);
      var answered = item.kind === "document" ? (item.documents || []).length > 0 : item.answer !== null && item.answer !== undefined;
      heading.appendChild(el("span", "tag" + (answered ? " done" : ""), answered ? this._t("saved") : item.required ? this._t("required") : this._t("optional")));
      card.appendChild(heading);
      card.appendChild(el("p", "reason", item.reason));
      var feedback = el("p", "error");
      var body = item.kind === "document" ? this._documentBody(item, feedback) : this._answerBody(item, feedback);
      card.appendChild(body);
      card.appendChild(feedback);
      return card;
    };

    OnboardingElement.prototype._answerBody = function (item, feedback) {
      var self = this;
      var form = el("div");
      var read;
      if (item.kind === "choice") {
        var choice = select(item.choices || [], item.answer, this._t("choose"));
        form.appendChild(field(item.label, choice));
        read = function () {
          return choice.value || null;
        };
      } else if (item.kind === "address") {
        var a = item.answer || {};
        var lines = a.lines || [];
        var street = input(lines[0], { autocomplete: "address-line1", maxlength: "120" });
        var street2 = input(lines[1], { autocomplete: "address-line2", maxlength: "120" });
        var city = input(a.locality, { autocomplete: "address-level2", maxlength: "80" });
        var region = input(a.region, { autocomplete: "address-level1", maxlength: "2" });
        var postal = input(a.postal_code, { autocomplete: "postal-code", maxlength: "10" });
        form.appendChild(field(this._t("street"), street));
        form.appendChild(field(this._t("street2"), street2));
        var row = el("div", "row");
        row.appendChild(field(this._t("city"), city));
        row.appendChild(field(this._t("region"), region));
        row.appendChild(field(this._t("postal"), postal));
        form.appendChild(row);
        read = function () {
          return {
            lines: [street.value, street2.value].filter(function (v) {
              return v.trim();
            }),
            locality: city.value,
            region: region.value,
            postal_code: postal.value,
          };
        };
      } else if (item.kind === "owners") {
        read = this._ownersForm(form, item.answer || [{}]);
      } else {
        var text = input(item.answer, { maxlength: item.kind === "url" ? "2048" : "160" });
        if (item.kind === "url") text.type = "url";
        form.appendChild(field(item.label, text));
        read = function () {
          return text.value;
        };
      }
      var save = el("button", null, this._t("save"));
      save.type = "button";
      save.addEventListener("click", function () {
        feedback.className = "error";
        feedback.textContent = "";
        save.disabled = true;
        var answers = {};
        answers[item.id] = read();
        self._client
          .answers(answers)
          .then(
            function (data) {
              var refused = data.errors && data.errors[item.id];
              if (refused) {
                feedback.textContent = refused.message;
                save.disabled = false;
                return;
              }
              self._update(data.application);
              var card = self._cards[item.id];
              var note = card && card.querySelector(".error");
              if (note) {
                note.className = "note";
                note.textContent = self._t("saved");
              }
            },
            function (error) {
              save.disabled = false;
              var text = self._fail(error);
              if (typeof text === "string") feedback.textContent = text;
            }
          );
      });
      form.appendChild(save);
      return form;
    };

    OnboardingElement.prototype._ownersForm = function (form, owners) {
      var self = this;
      var rows = [];
      var list = el("div");
      form.appendChild(list);

      function addRow(owner) {
        var box = el("div", "owner");
        var name = input(owner.name, { maxlength: "160", autocomplete: "name" });
        var kind = select(
          [
            { value: "person", label: self._t("person") },
            { value: "business", label: self._t("business") },
          ],
          owner.kind || "person"
        );
        var role = select(
          [
            { value: "owner", label: self._t("owner") },
            { value: "controller", label: self._t("controller") },
            { value: "owner_and_controller", label: self._t("both") },
          ],
          owner.role || "owner"
        );
        var pct = input(owner.ownership_pct, { inputmode: "decimal", maxlength: "6" });
        var dob = input(owner.date_of_birth, { maxlength: "10", placeholder: "1980-04-15" });
        box.appendChild(field(self._t("ownerName"), name));
        var row = el("div", "row");
        row.appendChild(field(self._t("ownerKind"), kind));
        row.appendChild(field(self._t("ownerRole"), role));
        row.appendChild(field(self._t("ownership"), pct));
        row.appendChild(field(self._t("dob"), dob));
        box.appendChild(row);
        var remove = el("button", "secondary", self._t("remove"));
        remove.type = "button";
        var entry = {
          read: function () {
            var value = { name: name.value, kind: kind.value, role: role.value };
            if (pct.value.trim()) value.ownership_pct = Number(pct.value);
            if (dob.value.trim() && kind.value === "person") value.date_of_birth = dob.value.trim();
            return value;
          },
        };
        remove.addEventListener("click", function () {
          if (rows.length <= 1) return;
          rows.splice(rows.indexOf(entry), 1);
          list.removeChild(box);
        });
        box.appendChild(remove);
        rows.push(entry);
        list.appendChild(box);
      }

      owners.forEach(addRow);
      var add = el("button", "secondary", this._t("addOwner"));
      add.type = "button";
      add.addEventListener("click", function () {
        addRow({});
      });
      form.appendChild(add);
      return function () {
        return rows.map(function (row) {
          return row.read();
        });
      };
    };

    OnboardingElement.prototype._documentBody = function (item, feedback) {
      var self = this;
      var body = el("div");
      var docs = el("ul", "docs");
      (item.documents || []).forEach(function (doc) {
        var li = el("li");
        li.appendChild(el("span", null, self._t("uploaded") + ": " + doc.filename));
        var remove = el("button", "secondary", self._t("remove"));
        remove.type = "button";
        remove.addEventListener("click", function () {
          remove.disabled = true;
          self._client.remove(doc.id).then(
            function (application) {
              self._update(application);
            },
            function (error) {
              remove.disabled = false;
              var text = self._fail(error);
              if (typeof text === "string") feedback.textContent = text;
            }
          );
        });
        li.appendChild(remove);
        docs.appendChild(li);
      });
      body.appendChild(docs);
      var file = el("input");
      file.type = "file";
      file.accept = "application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg";
      body.appendChild(field(this._t("chooseFile"), file));
      var upload = el("button", null, this._t("upload"));
      upload.type = "button";
      var suggestions = el("div", "suggest hidden");
      upload.addEventListener("click", function () {
        feedback.textContent = "";
        if (!file.files || !file.files[0]) return;
        upload.disabled = true;
        self._client.upload(item.id, file.files[0]).then(
          function (doc) {
            upload.disabled = false;
            self._client.application().then(function (application) {
              self._update(application);
              var card = self._cards[item.id];
              var box = card && card.querySelector(".suggest");
              if (box) self._suggest(box, doc);
            });
          },
          function (error) {
            upload.disabled = false;
            var text = self._fail(error);
            if (typeof text === "string") feedback.textContent = text;
          }
        );
      });
      body.appendChild(upload);
      body.appendChild(suggestions);
      return body;
    };

    OnboardingElement.prototype._suggest = function (box, doc) {
      var self = this;
      box.textContent = "";
      box.className = "suggest";
      var found = doc.suggestions || [];
      if (!found.length) {
        box.appendChild(el("p", null, this._t("nothingFound")));
        return;
      }
      box.appendChild(el("p", null, this._t("suggestions")));
      var labels = {};
      (this._app.items || []).forEach(function (i) {
        labels[i.id] = i.label;
      });
      var checks = found.map(function (s) {
        var label = el("label");
        var check = el("input");
        check.type = "checkbox";
        check.checked = true;
        check.value = s.item;
        label.appendChild(check);
        label.appendChild(el("span", null, (labels[s.item] || s.item) + ": " + s.value));
        box.appendChild(label);
        return check;
      });
      var use = el("button", null, this._t("useSelected"));
      use.type = "button";
      use.addEventListener("click", function () {
        var items = checks
          .filter(function (c) {
            return c.checked;
          })
          .map(function (c) {
            return c.value;
          });
        if (!items.length) return;
        use.disabled = true;
        self._client.confirm(doc.id, items).then(
          function (data) {
            box.className = "suggest hidden";
            self._update(data.application);
          },
          function (error) {
            use.disabled = false;
            var text = self._fail(error);
            if (typeof text === "string") box.appendChild(el("p", "error", text));
          }
        );
      });
      box.appendChild(use);
    };

    OnboardingElement.prototype._footer = function () {
      var self = this;
      var footer = el("div", "footer");
      var talk = el("button", "secondary", this._t("talk"));
      talk.type = "button";
      var talkBox = el("div", "hidden");
      var message = el("textarea");
      message.maxLength = 1000;
      message.rows = 3;
      talkBox.appendChild(field(this._t("talkPrompt"), message));
      var send = el("button", null, this._t("send"));
      send.type = "button";
      talkBox.appendChild(send);
      this._talkNote = el("p", "note");
      talk.addEventListener("click", function () {
        talkBox.className = "";
      });
      send.addEventListener("click", function () {
        send.disabled = true;
        self._client.person(message.value).then(
          function (application) {
            talkBox.className = "hidden";
            self._update(application);
            self.dispatchEvent(new CustomEvent("onboarding:person-requested"));
          },
          function (error) {
            send.disabled = false;
            var text = self._fail(error);
            if (typeof text === "string") self._talkNote.textContent = text;
          }
        );
      });
      footer.appendChild(talk);
      footer.appendChild(talkBox);
      footer.appendChild(this._talkNote);
      this._submit = el("button", null, this._t("submit"));
      this._submit.type = "button";
      this._submit.setAttribute("data-role", "submit");
      this._submitHint = el("p", "error");
      this._submit.addEventListener("click", function () {
        self._submit.disabled = true;
        self._client.submit().then(
          function (application) {
            self._render(application);
            self.dispatchEvent(new CustomEvent("onboarding:submitted"));
          },
          function (error) {
            self._submit.disabled = false;
            var text = self._fail(error);
            if (typeof text === "string") self._submitHint.textContent = text;
          }
        );
      });
      var submitRow = el("div");
      submitRow.appendChild(this._submit);
      submitRow.appendChild(this._submitHint);
      footer.appendChild(submitRow);
      return footer;
    };

    if (!root.customElements.get("agenticorg-onboarding")) {
      root.customElements.define("agenticorg-onboarding", OnboardingElement);
    }
  }

  root.AgenticOrgOnboarding = {
    createClient: createClient,
    linkTokenFrom: linkTokenFrom,
    Element: OnboardingElement,
    STRINGS: STRINGS,
    version: "0.1.0",
  };
})(typeof window !== "undefined" ? window : this);
