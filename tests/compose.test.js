// The coding page's message box rules: slash commands and @file mentions (plain node, no browser needed).
// usage: node tests/compose.test.js
const assert = require("assert"), path = require("path");
const C = require(path.join(__dirname, "..", "app", "static", "pages", "code", "compose.js"));
const names = (l) => l.map((c) => c.name).join(",");

// 1. the menu: "/" lists everything, letters narrow it, an alias finds its command, and it closes once a space is typed
assert.strictEqual(C.matchCommands("/").length, C.COMMANDS.length);
assert.strictEqual(names(C.matchCommands("/re")), "rewind,review");
assert.strictEqual(names(C.matchCommands("/cl")), "new", "the alias /clear finds /new");
assert.strictEqual(names(C.matchCommands("/TERM")), "terminal");
assert.deepStrictEqual(C.matchCommands("/mode "), []);
assert.deepStrictEqual(C.matchCommands("fix /re"), [], "only at the very start");
assert.deepStrictEqual(C.matchCommands(""), []);

// 2. parsing: a command with or without words after it; an unknown name is reported; a file path is NOT a command
let p = C.parseCommand("/mode auto");
assert.ok(p.command.name === "mode" && p.arg === "auto");
p = C.parseCommand("  /review look hard at the login code \n");
assert.ok(p.command.kind === "ask" && p.arg === "look hard at the login code");
assert.strictEqual(C.parseCommand("/clear").command.name, "new");
p = C.parseCommand("/nonsense");
assert.ok(p && p.command === null && p.name === "nonsense");
assert.strictEqual(C.parseCommand("/etc/hosts is wrong"), null);
assert.strictEqual(C.parseCommand("please /review"), null);
assert.strictEqual(C.parseCommand("/"), null);
for (const c of C.COMMANDS) {
  assert.ok(/^[a-z]+$/.test(c.name) && c.hint, c.name);
  assert.ok(c.kind === "page" || (c.kind === "ask" && c.prompt.length > 20), c.name);
  assert.strictEqual(C.find(c.name), c);
}

// 3. finding the mention at the caret
assert.deepStrictEqual(C.mentionAt("look at @src/ap", 15), { start: 8, query: "src/ap" });
assert.deepStrictEqual(C.mentionAt("@", 1), { start: 0, query: "" });
assert.deepStrictEqual(C.mentionAt("look at @src/app.py and more", 11), { start: 8, query: "sr" }, "caret in the middle of a mention");
assert.strictEqual(C.mentionAt("mail bob@example.com", 20), null, "an e-mail address is not a mention");
assert.strictEqual(C.mentionAt("see @a.py then", 14), null, "past the mention");
assert.strictEqual(C.mentionAt("no mention here", 15), null);
assert.deepStrictEqual(C.mentionAt("line one\n@rea", 13), { start: 9, query: "rea" }, "after a new line");

// 4. ranking: name starts with it, name contains it, path contains it, letters in order; shorter paths first; a limit
const FILES = ["README.md", "src/app.py", "src/apple/core.py", "docs/snapshot.md", "tests/test_app.py", "app/main.py", "a/p/p.txt"];
assert.deepStrictEqual(C.rankFiles(FILES, "app", 10), ["src/app.py", "tests/test_app.py", "app/main.py", "src/apple/core.py", "a/p/p.txt"]);
assert.deepStrictEqual(C.rankFiles(FILES, "READ", 10), ["README.md"]);
assert.deepStrictEqual(C.rankFiles(FILES, "zzz", 10), []);
assert.strictEqual(C.rankFiles(FILES, "", 3).length, 3);
assert.strictEqual(C.rankFiles(FILES, "", 3)[0], "README.md", "nothing typed: shortest paths first");

// 5. putting the chosen path in
let m = C.mentionAt("look at @src/ap", 15), r = C.applyMention("look at @src/ap", 15, m, "src/app.py");
assert.deepStrictEqual(r, { text: "look at @src/app.py ", caret: 20 });
m = C.mentionAt("fix @ma please", 7); r = C.applyMention("fix @ma please", 7, m, "app/main.py");
assert.deepStrictEqual(r, { text: "fix @app/main.py please", caret: 16 }, "no doubled space before the words that follow");

console.log("compose tests passed");
