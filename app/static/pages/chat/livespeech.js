/* Chat: where to cut a streaming answer so each sentence can be spoken as soon as it has printed (live read-aloud).
   Kept apart from the page so it can be tested without a browser. It only works on text.
   cut(raw, from, final) returns the index in `raw` up to which text is ready to speak, starting at `from`:
   - the end of the last complete sentence (a . ! or ? followed by a space, or a new line), never inside a code fence or an open `code span`;
   - the whole rest when `final` is true (the answer has ended);
   - `from` itself when no sentence is complete yet.
   The page waits for MIN_FIRST characters before the first piece and MIN_NEXT before later ones, so the voice does not stutter on tiny pieces. */
(function (root) {
  "use strict";

  var MIN_FIRST = 25, MIN_NEXT = 60;

  function cut(raw, from, final) {
    raw = String(raw || "");
    from = from || 0;
    if (final) return raw.length;
    var seg = raw.slice(from), re = /[.!?]["')\]]*(\s)|\n/g, m, end = -1;
    while ((m = re.exec(seg))) {
      var at = m.index + m[0].length, head = seg.slice(0, at);
      if ((head.match(/```/g) || []).length % 2 === 0 && (head.match(/`/g) || []).length % 2 === 0) end = at;
    }
    return end < 0 ? from : from + end;
  }

  var api = { cut: cut, MIN_FIRST: MIN_FIRST, MIN_NEXT: MIN_NEXT };
  root.__APP_CHAT_LIVE__ = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
