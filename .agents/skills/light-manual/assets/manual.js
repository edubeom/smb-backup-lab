/* 사용설명서 슬라이드 엔진 (dev-process-framework / dev-process-light 공용)
   <body data-manual="설명서 이름" data-index="index.html" data-chapters="index.html|목차;memo.html|메모;...">
   키: P 발표 모드 · N 대본 보기 · ←/→ 이동 (발표 모드) · Esc 나가기 · #3 처럼 주소 끝에 번호를 붙이면 그 장으로 */
(function () {
  "use strict";
  var body = document.body;
  var slides = Array.prototype.slice.call(document.querySelectorAll(".slide"));
  if (!slides.length) return;
  var cur = 0;

  // 슬라이드 안 내용을 .s 로 감싸고(크기 비율용) 대본 aside 는 밖에 둔다
  slides.forEach(function (sl, i) {
    sl.id = sl.id || "s" + (i + 1);
    if (!sl.querySelector(":scope > .s")) {
      var wrap = document.createElement("div");
      wrap.className = "s";
      Array.prototype.slice.call(sl.childNodes).forEach(function (n) {
        if (!(n.nodeType === 1 && n.tagName === "ASIDE" && n.classList.contains("notes"))) wrap.appendChild(n);
      });
      sl.insertBefore(wrap, sl.firstChild);
    }
  });
  var deck = document.createElement("main");
  deck.className = "m-deck";
  slides[0].parentNode.insertBefore(deck, slides[0]);
  slides.forEach(function (sl) { deck.appendChild(sl); });

  // 머리말: 설명서 이름 · 장 목록 · 버튼
  var bar = document.createElement("header");
  bar.className = "m-bar";
  var title = document.createElement("a");
  title.className = "m-title";
  title.textContent = body.dataset.manual || document.title;
  title.href = body.dataset.index || "#s1";
  bar.appendChild(title);
  if (body.dataset.chapters) {
    var nav = document.createElement("nav");
    var here = location.pathname.split("/").pop() || "index.html";
    body.dataset.chapters.split(";").forEach(function (pair) {
      var p = pair.split("|");
      if (!p[0]) return;
      var a = document.createElement("a");
      a.href = p[0].trim(); a.textContent = (p[1] || p[0]).trim();
      if (p[0].trim() === here) a.setAttribute("aria-current", "page");
      nav.appendChild(a);
    });
    bar.appendChild(nav);
  }
  function btn(label, fn) { var b = document.createElement("button"); b.type = "button"; b.textContent = label; b.onclick = fn; bar.appendChild(b); }
  btn("▶ 발표 (P)", function () { present(true); });
  btn("🎙 대본 (N)", function () { body.classList.toggle("show-notes"); });
  btn("🖨 인쇄·PDF", function () { window.print(); });
  body.insertBefore(bar, body.firstChild);

  function show(i) {
    cur = Math.max(0, Math.min(slides.length - 1, i));
    slides.forEach(function (sl, k) { sl.classList.toggle("current", k === cur); });
    if (history.replaceState) history.replaceState(null, "", "#" + (cur + 1));
  }
  function present(on) {
    body.classList.toggle("present", on);
    if (on) show(cur);
  }
  document.addEventListener("keydown", function (e) {
    if (e.target && /INPUT|TEXTAREA/.test(e.target.tagName)) return;
    var k = e.key;
    if (k === "p" || k === "P") present(!body.classList.contains("present"));
    else if (k === "n" || k === "N") body.classList.toggle("show-notes");
    else if (k === "Escape") present(false);
    else if (body.classList.contains("present")) {
      if (k === "ArrowRight" || k === "PageDown" || k === " ") { show(cur + 1); e.preventDefault(); }
      else if (k === "ArrowLeft" || k === "PageUp") { show(cur - 1); e.preventDefault(); }
      else if (k === "Home") show(0);
      else if (k === "End") show(slides.length - 1);
    }
  });
  document.addEventListener("click", function (e) {
    if (!body.classList.contains("present") || e.target.closest("a,button")) return;
    show(cur + (e.clientX > window.innerWidth / 3 ? 1 : -1));
  });
  var m = /^#(\d+)$/.exec(location.hash);
  if (m) { cur = parseInt(m[1], 10) - 1; var t = slides[cur]; if (t) t.scrollIntoView(); }
  if (/[?&]present\b/.test(location.search)) present(true);
  if (/[?&]export\b/.test(location.search)) body.classList.add("export");
  if (/[?&]sheet\b/.test(location.search)) body.classList.add("sheet");
})();
