/* Agentic coding, the message box helpers: slash commands and @file mentions.
   Kept apart from the page so they can be tested without a browser. Nothing here touches the page; it only works on text.
   - A message that starts with "/" is a command. Some act on the page (new session, rewind, switch pane); some are shorthand for a
     message to the agent (review, commit, test).
   - "@" followed by part of a file name offers the project's files; the chosen path stays in the message as @path and the agent is told
     to read the files named that way. */
(function (root) {
  "use strict";

  /* kind "page": handled in the page (see run() in index.js). kind "ask": sends `prompt` (plus anything typed after the command) to the agent. */
  var COMMANDS = [
    { name: "help", kind: "page", hint: "List these commands" },
    { name: "new", kind: "page", alias: ["clear"], hint: "Start a new session" },
    { name: "stop", kind: "page", hint: "Stop the run in progress" },
    { name: "rewind", kind: "page", hint: "Undo back to the latest restore point" },
    { name: "diff", kind: "page", alias: ["changes"], hint: "Show what has changed" },
    { name: "files", kind: "page", hint: "Show the project's files" },
    { name: "terminal", kind: "page", alias: ["term"], hint: "Show the terminal" },
    { name: "browser", kind: "page", hint: "Show the shared browser" },
    { name: "mode", kind: "page", args: "ask | auto", hint: "Ask before risky steps, or approve everything" },
    { name: "status", kind: "page", hint: "Branch, changed files and restore points" },
    { name: "review", kind: "ask", hint: "Have the agent review the uncommitted changes",
      prompt: "Review the uncommitted changes in this project. Look for bugs, risky edits and anything left unfinished. List what you find, most serious first. Do not change any files." },
    { name: "commit", kind: "ask", hint: "Have the agent commit the current changes",
      prompt: "Commit the current changes in this project with a short, clear commit message that says what changed and why. Do not push." },
    { name: "test", kind: "ask", hint: "Have the agent run the tests and report",
      prompt: "Find how this project's tests are run, run them, and tell me plainly what passed and what failed. Do not fix anything yet." },
    { name: "explain", kind: "ask", hint: "Have the agent explain this project",
      prompt: "Explain this project briefly: what it does, how it is laid out, and how to run it. Read the files you need first. Do not change any files." }
  ];

  function find(name) {
    name = String(name || "").toLowerCase();
    for (var i = 0; i < COMMANDS.length; i++) { var c = COMMANDS[i]; if (c.name === name || (c.alias || []).indexOf(name) >= 0) return c; }
    return null;
  }

  /* the commands to offer while the box holds only "/" and the start of a name */
  function matchCommands(text) {
    var m = /^\/([A-Za-z]*)$/.exec(String(text || ""));
    if (!m) return [];
    var want = m[1].toLowerCase();
    return COMMANDS.filter(function (c) { return c.name.indexOf(want) === 0 || (c.alias || []).some(function (a) { return want && a.indexOf(want) === 0; }); });
  }

  /* "/name rest" -> { command, arg }; null when the text is not a command. An unknown name gives { command: null, name }. A path such as
     "/etc/hosts is wrong" is not a command: the name must be letters only and end at a space or the end of the text. */
  function parseCommand(text) {
    var m = /^\/([A-Za-z]+)(?:[ \t]+([\s\S]*))?$/.exec(String(text || "").trim());
    if (!m) return null;
    return { command: find(m[1]), name: m[1].toLowerCase(), arg: (m[2] || "").trim() };
  }

  /* the @mention being typed at the caret: "@" at the start or after a space, then no spaces up to the caret */
  function mentionAt(text, caret) {
    text = String(text || "");
    if (typeof caret !== "number" || caret < 0 || caret > text.length) caret = text.length;
    var i = caret - 1;
    while (i >= 0 && !/\s/.test(text.charAt(i)) && text.charAt(i) !== "@") i--;
    if (i < 0 || text.charAt(i) !== "@") return null;
    if (i > 0 && !/\s/.test(text.charAt(i - 1))) return null;                 // an e-mail address, not a mention
    return { start: i, query: text.slice(i + 1, caret) };
  }

  /* best matches first: file name starts with the query, then file name contains it, then the path contains it, then the letters appear in order */
  function rankFiles(files, query, limit) {
    var qy = String(query || "").toLowerCase(), out = [];
    (files || []).forEach(function (f) {
      var p = String(f), low = p.toLowerCase(), name = low.slice(low.lastIndexOf("/") + 1), score;
      if (!qy) score = 3;
      else if (name.indexOf(qy) === 0) score = 0;
      else if (name.indexOf(qy) > 0) score = 1;
      else if (low.indexOf(qy) >= 0) score = 2;
      else { var j = 0; for (var k = 0; k < low.length && j < qy.length; k++) if (low.charAt(k) === qy.charAt(j)) j++; score = j === qy.length ? 4 : -1; }
      if (score >= 0) out.push({ path: p, score: score });
    });
    out.sort(function (a, b) { return a.score - b.score || a.path.length - b.path.length || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0); });
    return out.slice(0, limit || 8).map(function (o) { return o.path; });
  }

  /* put the chosen path in place of the half-typed mention; gives the new text and where the caret goes */
  function applyMention(text, caret, mention, path) {
    text = String(text || "");
    var before = text.slice(0, mention.start), after = text.slice(caret), ins = "@" + path + (/^\s/.test(after) ? "" : " ");
    return { text: before + ins + after, caret: (before + ins).length };
  }

  var api = { COMMANDS: COMMANDS, find: find, matchCommands: matchCommands, parseCommand: parseCommand, mentionAt: mentionAt, rankFiles: rankFiles, applyMention: applyMention };
  root.__APP_CODE_COMPOSE__ = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
