/* Chat: the approval card, kept apart from the page so it can be tested without the whole page.
   When the agent wants to run something that needs a yes, the page shows one card per request:
   - the FULL command is shown (never cut), because a person approving a command must see all of it;
   - "Approve once" is offered only when there is a command to approve; "Deny" whenever the request can be answered;
   - a request with no id, or one that is a question and not an approval, is shown but cannot be answered with a button;
   - one click sends one answer (no double submit); a failure re-enables the buttons and says why;
   - the answer is sent for the run the card was shown for, and refused if that run is no longer the current one.
   describe() works on plain data; makeCard() builds the React component from the page's own helpers. */
(function (root) {
  "use strict";

  function describe(control) {
    control = control || {};
    var rid = control.prompt_id || control.request_id || control.id || "";
    var command = typeof control.command === "string" ? control.command : "";
    var choices = Array.isArray(control.choices) ? control.choices : ["once", "deny"];
    var approval = !control.kind || control.kind === "approval";
    var answerable = !!(approval && rid);
    return {
      rid: rid,
      approval: approval,
      command: command,
      reason: control.description || control.detail || "",
      label: command ? "The agent wants to run this command" : (control.title || control.label || control.question || "Review the agent’s request"),
      answerable: answerable,
      canApprove: answerable && choices.indexOf("once") >= 0 && command.trim() !== "",
      canDeny: answerable && choices.indexOf("deny") >= 0
    };
  }

  /* deps: h, useState, useRef, errText (the page's own helpers). props: control, runId, onChoose(control, rid, choice, runId) -> Promise */
  function makeCard(deps) {
    var h = deps.h, useState = deps.useState, useRef = deps.useRef, errText = deps.errText;
    return function ApprovalCard(props) {
      var d = describe(props.control);
      var a = useState(""), decision = a[0], setDecision = a[1];
      var b = useState(""), error = b[0], setError = b[1];
      var submitted = useRef(false);

      function respond(choice) {
        if (submitted.current) return;
        submitted.current = true;
        setError("");
        setDecision(choice === "once" ? "Sending approval…" : "Sending denial…");
        props.onChoose(props.control, d.rid, choice, props.runId).then(function () {
          setDecision(choice === "once" ? "Approved once" : "Denied");
        }, function (failure) {
          submitted.current = false;
          setDecision("");
          setError(errText(failure));
        });
      }

      return h("section", { className: "atc-approval", "aria-label": "Approval required" },
        h("div", { className: "atc-approval-title" }, d.approval ? "Approval required" : "The agent is waiting for an answer"),
        h("div", { className: "atc-approval-label" }, d.label),
        d.reason ? h("p", { className: "atc-approval-reason" }, d.reason) : null,
        d.command ? h("pre", { className: "atc-approval-detail", tabIndex: 0, "aria-label": "Full command" }, d.command) : null,
        d.answerable ? h("div", { className: "atc-approval-actions" },
          d.canApprove ? h("button", { type: "button", className: "atc-btn atc-btn-primary", disabled: !!decision, onClick: function () { respond("once"); } }, "Approve once") : null,
          d.canDeny ? h("button", { type: "button", className: "atc-btn", disabled: !!decision, onClick: function () { respond("deny"); } }, "Deny") : null)
          : h("p", { className: "atc-muted" }, d.approval
            ? "This request can’t be answered here because it has no approval ID."
            : "Answer this in the chat where the agent asked the question."),
        decision ? h("p", { role: "status", className: "atc-muted" }, decision) : null,
        error ? h("p", { role: "alert", className: "atc-approval-reason" }, error) : null);
    };
  }

  var api = { describe: describe, makeCard: makeCard };
  root.__APP_CHAT_APPROVAL__ = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
