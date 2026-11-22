/* Live agent: the agent on a 3D stage, talking with you in real time.
   The character is drawn in this browser (TalkingHead and Three.js, both MIT, in engine.js). You type, she answers in her own voice
   with her lips following every word, and her mood, gestures and idle life follow what is happening. The character file is the
   install's own (atrium/avatar/avatar.glb on the server) and is never part of the project.
   Push-to-talk records in the browser and is transcribed by the same speech route as Chat.
   The stage code is adapted from the Atrium Loom avatar plugin; the conversation uses the same agent API as Chat. */
(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__, REG = window.__HERMES_PLUGINS__, UI = window.__APP_UI__;
  if (!SDK || !REG || !UI) return;
  var h = UI.h, useState = SDK.hooks.useState, useEffect = SDK.hooks.useEffect, useRef = SDK.hooks.useRef;
  var fetchJSON = SDK.fetchJSON;
  var AV = "/api/plugins/atrium-avatar", CHAT = "/api/plugins/atrium-chat";
  var TERMINAL = { completed: 1, failed: 1, cancelled: 1, interrupted: 1 };

  var enginePromise = null;
  function loadEngine() { if (!enginePromise) enginePromise = import("/static/pages/live/engine.js"); return enginePromise; }

  var MOODS = ["neutral", "happy", "love", "sad", "angry", "fear", "disgust", "sleep"];
  var GESTURES = [["handup", "Hand up"], ["index", "Point"], ["ok", "OK"], ["thumbup", "Thumbs up"], ["thumbdown", "Thumbs down"], ["shrug", "Shrug"], ["side", "Side"]];
  var VIEWS = [["full", "Full body"], ["mid", "Mid"], ["upper", "Upper body"], ["head", "Head"]];
  var DESK_VIEWS = [["desk", "At the desk"], ["close", "Close up"], ["board", "At the board"]];
  var REACTS = [["send", "You send"], ["work", "Starts work"], ["answer", "Answer ready"], ["approval", "Needs approval"], ["done", "Job done"], ["error", "Error"], ["back", "Welcome back"]];
  var IDLES = [["stretch", "Stretch"], ["coffee", "Coffee"], ["tidyNote", "Tidy a note"], ["readBoard", "Read the board"], ["fidget", "Fidget"], ["lookOut", "Look out"], ["leanBack", "Lean back"]];
  var STATE_TEXT = { idle: "Idle and ready", working: "Working", waiting: "Waiting for your approval", offline: "Offline" };

  function pref(key, dflt) { try { var v = localStorage.getItem("atrium.avatar." + key); return v === null ? dflt : v; } catch (x) { return dflt; } }
  function setPref(key, v) { try { localStorage.setItem("atrium.avatar." + key, String(v)); } catch (x) { /* optional */ } }
  function b64ToBuf(b64) { var s = atob(b64), n = s.length, u = new Uint8Array(n); for (var i = 0; i < n; i++) u[i] = s.charCodeAt(i); return u.buffer; }
  function errText(e) { var m = String((e && e.message) || e || "Something went wrong"), i = m.indexOf("{"); if (i >= 0) { try { var j = JSON.parse(m.slice(i)); if (j && typeof j.detail === "string") return j.detail; } catch (x) { /* fall through */ } } return m.replace(/^\d+:\s*/, "").slice(0, 200); }
  function newSession() { var s = "atrium-chat-live"; for (var i = 0; i < 10; i++) s += "abcdefghijklmnopqrstuvwxyz0123456789".charAt(Math.floor(Math.random() * 36)); return s; }

  function Page() {
    var cfg = UI.config(), agent = cfg.agentName || "Assistant";
    var ph_ = useState("loading"), phase = ph_[0], setPhase = ph_[1];            // loading | nomodel | ready | error
    var msg_ = useState(""), msg = msg_[0], setMsg = msg_[1];
    var sc_ = useState(pref("scene", "desk") === "plain" ? "plain" : "desk"), sceneMode = sc_[0], setSceneMode = sc_[1];
    var vw_ = useState(sc_[0] === "plain" ? "upper" : "desk"), view = vw_[0], setView = vw_[1];
    var mood_ = useState("neutral"), mood = mood_[0], setMood = mood_[1];
    var auto_ = useState(true), auto = auto_[0], setAuto = auto_[1];
    var idle_ = useState(pref("idle", "1") !== "0"), idleLife = idle_[0], setIdleLife = idle_[1];
    var gest_ = useState(pref("gestures", "1") !== "0"), gestOn = gest_[0], setGestOn = gest_[1];
    var feel_ = useState(pref("mood", "1") !== "0"), feelOn = feel_[0], setFeelOn = feel_[1];
    var aloud_ = useState(pref("aloud", "1") !== "0"), aloud = aloud_[0], setAloud = aloud_[1];
    var cust_ = useState(false), custom = cust_[0], setCustom = cust_[1];
    var voice_ = useState(null), voice = voice_[0], setVoice = voice_[1];
    var chat_ = useState([]), chat = chat_[0], setChat = chat_[1];               // [{role, text, error}]
    var txt_ = useState(""), text = txt_[0], setText = txt_[1];
    var run_ = useState(null), run = run_[0], setRun = run_[1];                   // { id, status, controls, gen }
    var sp_ = useState(false), speaking = sp_[0], setSpeaking = sp_[1];
    var cap_ = useState(""), caption = cap_[0], setCaption = cap_[1];
    var poll = UI.usePoll("/api/overview", 10000), ov = poll[0];
    var mic_ = useState("idle"), mic = mic_[0], setMic = mic_[1];                     // idle | start | rec | busy
    var secs_ = useState(0), micSecs = secs_[0], setMicSecs = secs_[1];
    var rec = useRef({ recorder: null, stream: null, starting: false, timer: null, byKey: false, alive: true, pointerDown: false, keyDown: false, want: false }), submitRef = useRef(null);
    var stageRef = useRef(null), headRef = useRef(null), deskRef = useRef(null), capBuf = useRef([]), timers = useRef({}), session = useRef(newSession()), logRef = useRef(null);
    var reactor = useRef(null), stopWatch = useRef(null), stopTool = useRef(null), prevAgent = useRef(null), agentRef = useRef("idle");
    var live = !!(run && !TERMINAL[run.status]);

    var ag = (ov && ov.agent) || {};
    var base = ag.running === false ? "offline" : (ag.busy || ag.active_agents) ? "working" : "idle";
    var agentState = run && run.status === "waiting_for_approval" ? "waiting" : live ? "working" : base;
    agentRef.current = agentState;

    // 1. start the engine and load the character
    useEffect(function () {
      var dead = false;
      setPhase("loading"); setMsg("");
      fetchJSON(AV + "/status").then(function (s) {
        if (dead) return null;
        if (!s.model) { setPhase("nomodel"); return null; }
        return loadEngine().then(function (eng) {
          if (dead) return;
          var head = new eng.TalkingHead(stageRef.current, { lipsyncModules: [], cameraView: sceneMode === "desk" ? "full" : "upper", modelFPS: 30, avatarMood: "neutral" });
          head.lipsync.en = new eng.LipsyncEn();
          headRef.current = head;
          return head.showAvatar({ url: AV + "/model", body: "F", avatarMood: "neutral", lipsyncLang: "en", baseline: { headRotateX: -0.05, eyeBlinkLeft: 0.15, eyeBlinkRight: 0.15 } },
            function (e) { if (!dead && e && e.lengthComputable) setMsg("Loading " + Math.round(e.loaded / e.total * 100) + "%"); }).then(function () {
            if (dead) return;
            if (sceneMode === "desk") {
              var d = deskRef.current = eng.installDesk(eng.THREE, head);
              d.setIdleLife(pref("idle", "1") !== "0"); d.setGestures(pref("gestures", "1") !== "0"); d.setFeelings(pref("mood", "1") !== "0");
              d.loadClips(AV + "/clips/ual1").catch(function () { /* no clip library installed */ });
              var notes = function () { fetchJSON(AV + "/projects").then(function (r) { if (deskRef.current) deskRef.current.setBoardNotes((r && r.notes) || []); }, function () { /* keep what is pinned */ }); };
              notes(); timers.current.notes = setInterval(notes, 60000);
              // her reactions to what happens (work starts, approval, job done, failures and being fed up, you coming back)
              if (window.__AVATAR_REACT__) {
                reactor.current = window.__AVATAR_REACT__.create(function () { return deskRef.current; }, {
                  sound: function () { return pref("aloud", "1") !== "0"; }, speakUrl: AV + "/speak",
                  onCaption: function (w) { if (w === "" || w === "#@%&!") { capBuf.current = []; setCaption(w); } else { capBuf.current.push(String(w || "").trim()); setCaption(capBuf.current.slice(-8).join(" ")); } }
                });
                stopWatch.current = reactor.current.watch();
              }
              // the whiteboard magnet for the tool she is really using glows
              if (window.__AVATAR_TOOL__) stopTool.current = window.__AVATAR_TOOL__.feed(function () { return deskRef.current; }, function () { return agentRef.current === "working"; });
            }
            setMsg(""); setPhase("ready");
          });
        });
      }).catch(function (e) { if (!dead) { setPhase("error"); setMsg(errText(e)); } });
      return function () {
        dead = true;
        var t = timers.current; Object.keys(t).forEach(function (k) { clearInterval(t[k]); clearTimeout(t[k]); });
        try { if (stopWatch.current) stopWatch.current(); } catch (x) { /* already stopped */ }
        try { if (stopTool.current) stopTool.current(); } catch (x) { /* already stopped */ }
        stopWatch.current = null; stopTool.current = null; reactor.current = null;
        try { if (deskRef.current) deskRef.current.dispose(); } catch (x) { /* already gone */ }
        deskRef.current = null;
        try { if (headRef.current) { headRef.current.stopSpeaking(); headRef.current.dispose(); } } catch (x) { /* already gone */ }
        headRef.current = null;
      };
    }, [sceneMode]);

    // 2. her mood and behaviour follow what the agent is doing (unless switched off)
    useEffect(function () {
      if (phase !== "ready" || !auto) return;
      try {
        if (deskRef.current) { deskRef.current.setState(agentState); setMood(agentState === "offline" ? "sleep" : "neutral"); }
        else if (headRef.current) {
          if (agentState === "offline") { headRef.current.setMood("sleep"); setMood("sleep"); } else { headRef.current.setMood("neutral"); setMood("neutral"); }
          if (agentState === "waiting") headRef.current.playGesture("handup", 4);
        }
      } catch (x) { /* a state change must never break the page */ }
    }, [agentState, auto, phase, sceneMode]);

    // keep the transcript scrolled to the newest line
    useEffect(function () { var el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [chat.length, caption]);

    function head() { return headRef.current; }
    function react(k) { try { if (deskRef.current) deskRef.current.react(k); } catch (x) { /* optional */ } }
    function reactEv(k) { if (reactor.current) reactor.current.event(k); else react(k); }      // through the shared reactions (repeated failures make her fed up)

    // what the agent is doing changed: work started, approval needed, work finished (also for jobs and cards that run without this page)
    useEffect(function () {
      if (phase !== "ready") return;
      var prev = prevAgent.current; prevAgent.current = agentState;
      if (prev !== null && reactor.current) reactor.current.state(prev, agentState);
    }, [agentState, phase]);

    // 3. the reply is read aloud, her lips following the words
    function say(t) {
      if (!t || !head()) return Promise.resolve();
      setSpeaking(true); capBuf.current = []; setCaption("");
      if (deskRef.current) deskRef.current.setSpeaking(true);
      var done = function () { setSpeaking(false); setCaption(""); if (deskRef.current) deskRef.current.setSpeaking(false); };
      return fetchJSON(AV + "/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t }) }).then(function (j) {
        var hd = head();
        if (!hd) { done(); return null; }
        return hd.audioCtx.resume().then(function () { return hd.audioCtx.decodeAudioData(b64ToBuf(j.audio)); }).then(function (audio) {
          var r = { audio: audio, words: [], wtimes: [], wdurations: [] };
          (j.words || []).forEach(function (w) { r.words.push(w.w); r.wtimes.push(Math.max(0, w.start - 60)); r.wdurations.push(Math.max(40, w.end - w.start)); });
          hd.lookAtCamera(400);
          if (deskRef.current) { try { deskRef.current.express(t, j.words || []); } catch (x) { /* gestures are optional */ } }
          hd.speakAudio(r, { lipsyncLang: "en" }, function (s) { capBuf.current.push(String(s || "").trim()); setCaption(capBuf.current.slice(-8).join(" ")); });
          setTimeout(done, Math.ceil(audio.duration * 1000) + 400);
        });
      }).catch(function (e) { setMsg(errText(e)); done(); });
    }
    function stopSpeaking() { try { head().stopSpeaking(); } catch (x) { /* not ready */ } setSpeaking(false); setCaption(""); if (deskRef.current) deskRef.current.setSpeaking(false); }

    // 4. the conversation: send, follow the run, show and speak the answer
    function push(m) { setChat(function (c) { return c.concat([m]); }); }
    function send() { submit(text.trim()); }
    function submit(t) {
      if (!t || live) return;
      setMsg(""); setText(""); stopSpeaking(); push({ role: "user", text: t }); reactEv("send");
      fetchJSON(CHAT + "/send", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_id: session.current, message: t }) })
        .then(function (r) { setRun({ id: r.run_id, status: r.status || "started", controls: [] }); },      // "work" and "approval" follow from her state below
          function (e) { push({ role: "assistant", text: errText(e), error: true }); reactEv("error"); });
    }
    useEffect(function () {
      if (!live) return undefined;
      var alive = true, timer;
      function tick() {
        fetchJSON(CHAT + "/runs/" + run.id).then(function (r) {
          if (!alive) return;
          var next = { id: run.id, status: r.status, controls: r.pending_controls || [], gen: r.execution_generation };
          if (TERMINAL[r.status]) {
            var out = String(r.output || "").trim();
            if (r.status === "completed" && out) { push({ role: "assistant", text: out }); if (reactor.current) reactor.current.answered(); react("answer"); if (aloud) say(out); }
            else push({ role: "assistant", text: r.status === "completed" ? "(no answer)" : (r.error || "That did not finish (" + r.status + ")."), error: r.status !== "completed" });
            if (r.status !== "completed") reactEv("error");
          }
          setRun(next);
        }, function (e) { if (alive) setMsg(errText(e)); }).then(function () { if (alive) timer = setTimeout(tick, 900); });
      }
      timer = setTimeout(tick, 700);
      return function () { alive = false; clearTimeout(timer); };
    }, [run && run.id, live]);
    function decide(ctl, choice) {
      fetchJSON(CHAT + "/runs/" + run.id + "/approval", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ request_id: ctl.request_id || ctl.prompt_id || ctl.id, execution_generation: run.gen, choice: choice }) }).catch(function (e) { setMsg(errText(e)); });
    }
    function stopRun() { if (run) fetchJSON(CHAT + "/runs/" + run.id + "/stop", { method: "POST" }).catch(function () { /* already over */ }); }
    function fresh() { stopSpeaking(); session.current = newSession(); setChat([]); setRun(null); setMsg(""); }

    // 5. tap-to-talk: tap the button (or press Space) to start, speak, tap again to send; the words are transcribed by the speech model Hermes uses and sent at once
    submitRef.current = submit;
    var micOk = !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    var MIC_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    function release() { var r = rec.current, ss = r.stream; r.stream = null; if (ss) ss.getTracks().forEach(function (tr) { try { tr.stop(); } catch (x) { /* already ended */ } }); }
    function micStop() {
      var r = rec.current;
      if (r.timer) { clearInterval(r.timer); r.timer = null; }
      if (r.recorder && r.recorder.state !== "inactive") { try { r.recorder.stop(); } catch (x) { /* already stopped */ } }
      release();
    }
    function micTranscribe(dataUrl, mime) {
      fetchJSON("/api/audio/transcribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data_url: dataUrl, mime_type: mime }) }).then(function (res) {
        if (!rec.current.alive) return;
        setMic("idle");
        var t = String((res && res.transcript) || "").trim();
        // the speech model is a chat model: on silence or noise it can describe the audio instead of transcribing it
        if (/\b(audio|recording|clip)\b[^.]*\b(noisy|noise|silen|static|unclear|inaudible|no (discernible |clear )?speech|difficult)\b/i.test(t) || /\b(clearer version|unable to transcribe|cannot transcribe)\b/i.test(t)) t = "";
        if (t) submitRef.current(t); else setMsg("I did not hear any speech. Tap the button, speak, then tap it again.");
      }, function (x) { if (!rec.current.alive) return; setMic("idle"); setMsg("Transcription failed: " + errText(x)); });
    }
    function micStart(byKey) {
      var r = rec.current;
      if (r.starting || r.stream || phase !== "ready" || live) return;
      r.starting = true; r.byKey = !!byKey; setMsg(""); setMic("start"); stopSpeaking();
      try { head().lookAtCamera(600); } catch (x) { /* optional */ }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        r.starting = false;
        if (!r.alive || !r.want) { stream.getTracks().forEach(function (tr) { tr.stop(); }); setMic("idle"); return; }   // tapped again before the microphone was ready
        r.stream = stream;
        var type = "";
        for (var i = 0; i < MIC_TYPES.length; i++) if (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(MIC_TYPES[i])) { type = MIC_TYPES[i]; break; }
        var recorder;
        try { recorder = type ? new window.MediaRecorder(stream, { mimeType: type }) : new window.MediaRecorder(stream); } catch (x) { release(); setMic("idle"); setMsg("This browser cannot record audio."); return; }
        var chunks = [], started = Date.now();
        recorder.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
        recorder.onstop = function () {
          release();
          if (!chunks.length || Date.now() - started < 600) { setMic("idle"); if (r.alive) setMsg("That was too short. Tap the button, speak, then tap it again."); return; }
          setMic("busy");
          var blob = new Blob(chunks, { type: recorder.mimeType || type || "audio/webm" }), rd = new FileReader();
          rd.onload = function () { micTranscribe(rd.result, String(blob.type || "audio/webm").split(";")[0]); };
          rd.onerror = function () { setMic("idle"); setMsg("Could not read the recording."); };
          rd.readAsDataURL(blob);
        };
        r.recorder = recorder; recorder.start(); setMicSecs(0); setMic("rec");
        r.timer = setInterval(function () { var n = Math.round((Date.now() - started) / 1000); setMicSecs(n); if (n >= 60) micStop(); }, 250);
      }, function (x) {
        r.starting = false; setMic("idle");
        setMsg(x && x.name === "NotAllowedError" ? "The browser blocked the microphone. Allow it for this site and try again." : x && x.name === "NotFoundError" ? "No microphone was found." : "Could not start the microphone.");
      });
    }
    function toggleMic() {                        // one tap starts, the next tap stops and sends
      var r = rec.current;
      if (r.starting) { r.want = false; return; }
      if (r.stream) { r.want = false; micStop(); return; }
      r.want = true; micStart(false);
    }
    useEffect(function () {                       // press Space anywhere outside a text box to start or stop talking
      var r = rec.current; r.alive = true;
      function typing(t) { return t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.tagName === "SELECT" || t.isContentEditable); }
      function down(ev) { if (ev.code !== "Space" || ev.repeat || ev.ctrlKey || ev.metaKey || ev.altKey || typing(ev.target) || (ev.target && ev.target.tagName === "BUTTON")) return; ev.preventDefault(); toggleMic(); }
      window.addEventListener("keydown", down);
      return function () {
        window.removeEventListener("keydown", down);
      };
    }, [phase, live]);
    useEffect(function () {                       // leaving the page: stop recording and let go of the microphone
      var r = rec.current; r.alive = true;
      return function () {
        r.alive = false;
        if (r.timer) clearInterval(r.timer);
        if (r.recorder && r.recorder.state !== "inactive") { try { r.recorder.onstop = null; r.recorder.stop(); } catch (x) { /* already stopped */ } }
        release();
      };
    }, []);

    // customize
    useEffect(function () { if (custom && !voice) fetchJSON(CHAT + "/voice").then(setVoice).catch(function () { /* optional */ }); }, [custom]);
    function chooseVoice(id) { fetchJSON(CHAT + "/voice", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ voice: id }) }).then(function (r) { setVoice(Object.assign({}, voice, { voice: r.voice })); }).catch(function (e) { setMsg(errText(e)); }); }
    function pickMood(m) { setMood(m); setAuto(false); try { if (deskRef.current) deskRef.current.setMood(m); else head().setMood(m); } catch (x) { /* not ready */ } }
    function pickView(v) { setView(v); try { if (deskRef.current) deskRef.current.setFrame(v, 800); else head().setView(v); } catch (x) { /* not ready */ } }
    function pickScene(m) { setPref("scene", m); setView(m === "desk" ? "desk" : "upper"); setSceneMode(m); }
    function toggle(key, set, apply) { return function (e) { var v = e.target.checked; set(v); setPref(key, v ? "1" : "0"); try { if (apply && deskRef.current) deskRef.current[apply](v); } catch (x) { /* ignore */ } }; }

    function Check(p) { return h("label", { className: "lv-check" }, h("input", { type: "checkbox", checked: p.on, onChange: p.onChange }), " " + p.label); }
    function Chip(p) { return h("button", { type: "button", className: "lv-chip" + (p.on ? " lv-on" : ""), onClick: p.onClick }, p.label); }

    var panel = custom ? h("aside", { className: "lv-custom", "aria-label": "Customize" },
      h("div", { className: "lv-custom-head" }, h("h2", null, "Customize"), h("button", { type: "button", className: "lv-btn", onClick: function () { setCustom(false); } }, "Close")),
      h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Scene"),
        h("select", { className: "lv-select", value: sceneMode, "aria-label": "Scene", onChange: function (e) { pickScene(e.target.value); } }, h("option", { value: "desk" }, "At her desk"), h("option", { value: "plain" }, "Plain stage"))),
      h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Camera"),
        h("select", { className: "lv-select", value: view, "aria-label": "Camera view", onChange: function (e) { pickView(e.target.value); } }, (sceneMode === "desk" ? DESK_VIEWS : VIEWS).map(function (v) { return h("option", { key: v[0], value: v[0] }, v[1]); }))),
      h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Voice"),
        h(Check, { on: aloud, label: "Read her answers aloud", onChange: toggle("aloud", setAloud) }),
        voice ? h("select", { className: "lv-select", value: voice.voice, "aria-label": "Voice", onChange: function (e) { chooseVoice(e.target.value); } }, voice.choices.map(function (c) { return h("option", { key: c.id, value: c.id }, c.label); })) : null),
      h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Behaviour"),
        h(Check, { on: auto, label: "Follow what " + agent + " is doing", onChange: function (e) { setAuto(e.target.checked); } }),
        sceneMode === "desk" ? h(Check, { on: idleLife, label: "Idle life (coffee, stretching, reading the board)", onChange: toggle("idle", setIdleLife, "setIdleLife") }) : null,
        sceneMode === "desk" ? h(Check, { on: gestOn, label: "Talking gestures", onChange: toggle("gestures", setGestOn, "setGestures") }) : null,
        sceneMode === "desk" ? h(Check, { on: feelOn, label: "Moods that build and fade", onChange: toggle("mood", setFeelOn, "setFeelings") }) : null),
      h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Try a mood"), h("div", { className: "lv-chips" }, MOODS.map(function (m) { return h(Chip, { key: m, on: mood === m, label: m, onClick: function () { pickMood(m); } }); }))),
      h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Try a gesture"), h("div", { className: "lv-chips" }, GESTURES.map(function (g) { return h(Chip, { key: g[0], label: g[1], onClick: function () { try { head().playGesture(g[0], 3); } catch (x) { /* not ready */ } } }); }))),
      sceneMode === "desk" ? h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Try a reaction"), h("div", { className: "lv-chips" }, REACTS.map(function (r) { return h(Chip, { key: r[0], label: r[1], onClick: function () { react(r[0]); } }); }))) : null,
      sceneMode === "desk" ? h("div", { className: "lv-field" }, h("div", { className: "lv-label" }, "Try an idle move"), h("div", { className: "lv-chips" }, IDLES.map(function (r) { return h(Chip, { key: r[0], label: r[1], onClick: function () { try { deskRef.current.act(r[0]); } catch (x) { /* never break the page */ } } }); }))) : null) : null;

    var controls = run && run.controls && run.controls.length ? h("div", { className: "lv-approve", role: "alert" },
      h("div", null, agent + " needs your approval:"),
      run.controls.map(function (ctl, i) {
        return h("div", { key: i, className: "lv-approve-row" }, h("span", null, String(ctl.title || ctl.description || ctl.prompt || ctl.command || "an action")),
          h("button", { type: "button", className: "lv-btn lv-primary", onClick: function () { decide(ctl, "once"); } }, "Allow once"),
          h("button", { type: "button", className: "lv-btn", onClick: function () { decide(ctl, "deny"); } }, "Deny"));
      })) : null;

    return h("div", { className: "lv-page" },
      h("div", { className: "lv-top" },
        h("div", null, h("h1", null, "Live agent"), h("div", { className: "lv-state lv-state-" + agentState }, h("i", { className: "lv-dot" }), STATE_TEXT[agentState] || agentState)),
        h("div", { className: "lv-top-btns" },
          h("button", { type: "button", className: "lv-btn", onClick: fresh, disabled: !chat.length && !live }, "New conversation"),
          h("button", { type: "button", className: "lv-btn lv-primary", "aria-expanded": custom ? "true" : "false", onClick: function () { setCustom(!custom); } }, "✦ Customize"))),
      phase === "nomodel" ? h("div", { className: "lv-note" }, h("strong", null, "No character file yet. "), "Put a character file (a .glb with face shapes for talking) at ", h("code", null, "atrium/avatar/avatar.glb"), " in the data folder and reload. It stays on your server.") : null,
      phase === "error" ? h("div", { className: "lv-note lv-bad" }, "Could not start the stage: " + msg) : null,
      h("div", { className: "lv-main" + (custom ? " lv-with-custom" : "") },
        h("div", { className: "lv-stage-wrap" + (phase === "ready" || phase === "loading" ? "" : " lv-hidden") },
          h("div", { className: "lv-stage", ref: stageRef }),
          phase === "loading" ? h("div", { className: "lv-loading" }, msg || "Loading " + agent + "…") : null,
          caption ? h("div", { className: "lv-caption" }, caption) : null),
        h("section", { className: "lv-talk", "aria-label": "Conversation" },
          h("div", { className: "lv-log", ref: logRef, role: "log", "aria-live": "polite" },
            chat.length ? chat.map(function (m, i) { return h("div", { key: i, className: "lv-msg lv-" + m.role + (m.error ? " lv-error" : "") }, m.text); })
              : h("p", { className: "lv-empty" }, "Tap the microphone button (or press the Space bar), or type, and say something to " + agent + ". She answers out loud.")),
          controls,
          msg && phase === "ready" ? h("div", { className: "lv-err", role: "alert" }, msg) : null,
          h("div", { className: "lv-say" },
            h("textarea", { className: "lv-text", rows: 2, maxLength: 4000, "aria-label": "Message", placeholder: "Message " + agent + "…  (Enter to send)", value: text, disabled: phase !== "ready",
              onChange: function (e) { setText(e.target.value); }, onKeyDown: function (e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } } }),
            h("div", { className: "lv-say-btns" },
              micOk ? h("button", { type: "button", className: "lv-btn lv-mic" + (mic === "rec" ? " lv-rec" : ""), disabled: phase !== "ready" || live || mic === "busy",
                "aria-label": mic === "rec" ? "Tap to send" : "Tap to talk", title: "Tap to talk (or press Space), tap again to send",
                onClick: toggleMic, onContextMenu: function (e) { e.preventDefault(); } },
                mic === "rec" ? "● " + micSecs + "s · tap to send" : mic === "busy" ? "Transcribing…" : mic === "start" ? "Mic…" : "🎤 Tap to talk") : null,
              h("button", { type: "button", className: "lv-btn lv-primary", disabled: phase !== "ready" || live || !text.trim(), onClick: send }, live ? "Working…" : "Send"),
              live ? h("button", { type: "button", className: "lv-btn", onClick: stopRun }, "Stop work") : null,
              speaking ? h("button", { type: "button", className: "lv-btn", onClick: stopSpeaking }, "Be quiet") : null))),
        panel));
  }

  REG.register("live-page", Page);
})();
