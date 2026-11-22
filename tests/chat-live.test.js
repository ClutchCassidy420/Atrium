// Chat, live read-aloud: where a streaming answer is cut so each sentence can be spoken as soon as it has printed (plain node, no browser).
// usage: node tests/chat-live.test.js
const assert = require("assert"), path = require("path");
const L = require(path.join(__dirname, "..", "app", "static", "pages", "chat", "livespeech.js"));

assert.ok(L.MIN_FIRST > 0 && L.MIN_NEXT > L.MIN_FIRST, "the first piece may be shorter than later ones");

// 1. nothing is ready until a sentence is complete
assert.strictEqual(L.cut("Hello there", 0, false), 0, "no full stop yet");
assert.strictEqual(L.cut("Hello there.", 0, false), 0, "a full stop with nothing after it may be part of a number or a name still arriving");
assert.strictEqual(L.cut("", 0, false), 0);
assert.strictEqual(L.cut(undefined, 0, false), 0);

// 2. a complete sentence is ready, and the cut is after its trailing space
let raw = "Hello there. How are";
assert.strictEqual(raw.slice(0, L.cut(raw, 0, false)), "Hello there. ");
raw = "One. Two! Three? Four";
assert.strictEqual(raw.slice(0, L.cut(raw, 0, false)), "One. Two! Three? ", "the last complete sentence ends the piece");
assert.strictEqual("Line one\nLine tw".slice(0, L.cut("Line one\nLine tw", 0, false)), "Line one\n", "a new line is a boundary too");
raw = 'She said "stop." Then left';
assert.strictEqual(raw.slice(0, L.cut(raw, 0, false)), 'She said "stop." ', "closing quotes and brackets stay with their sentence");
raw = "(See above.) Next";
assert.strictEqual(raw.slice(0, L.cut(raw, 0, false)), "(See above.) ");

// 3. it continues from where the last piece ended
raw = "First one. Second one. Third";
const a = L.cut(raw, 0, false);
assert.strictEqual(raw.slice(0, a), "First one. Second one. ");
assert.strictEqual(L.cut(raw, a, false), a, "nothing new until another sentence is complete");
raw += " part. And";
const b = L.cut(raw, a, false);
assert.strictEqual(raw.slice(a, b), "Third part. ");

// 4. code is never cut in the middle, so it is not read out as half a block
raw = "Run this. ```js\nconst a = 1. Then more. \n";
assert.strictEqual(raw.slice(0, L.cut(raw, 0, false)), "Run this. ", "a boundary inside an open code fence is not a boundary");
raw = "Run this. ```js\nconst a = 1;\n``` Done. Next";
assert.strictEqual(raw.slice(0, L.cut(raw, 0, false)), "Run this. ```js\nconst a = 1;\n``` Done. ", "once the fence closes, text after it can be spoken");
raw = "Use `foo. bar` now. Then";
assert.strictEqual(raw.slice(0, L.cut(raw, 0, false)), "Use `foo. bar` now. ", "a stop inside a code span is not a boundary");
raw = "Use `foo. ba";
assert.strictEqual(L.cut(raw, 0, false), 0, "an open code span holds everything back");

// 5. when the answer has ended, everything left is ready
assert.strictEqual(L.cut("Unfinished thought", 0, true), "Unfinished thought".length);
assert.strictEqual(L.cut("A. B", 2, true), 4);
assert.strictEqual(L.cut("", 0, true), 0);

console.log("chat live OK: sentence boundaries, resuming, code protection, final flush");
