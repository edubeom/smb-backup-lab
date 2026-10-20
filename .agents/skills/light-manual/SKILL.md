---
name: light-manual
description: 사용설명서를 만들고, 고치고, 보여 줄 때 쓴다. 처음 만들 때·새 기능을 추가했을 때·사용 방법이 바뀌었을 때 저장(light-save) 전에 manual 폴더의 설명서를 HTML 슬라이드(웹페이지, 16:9 페이지마다 그림과 강의 대본, 한 단계 = 한 페이지)로 만들거나 고치고, 끝나면 바뀐 부분을 채팅에 보여 준다. "사용설명서 보여 줘", "매뉴얼", "사용법 알려 줘", "설명서 pptx 로" 라고 할 때도 쓴다.
---

# light-manual — 사용설명서

이 사이트를 **쓰는 사람**(나중의 나, 가족, 친구 -- 개발자가 아님)이 그림만 따라가도 쓸 수 있게 한다.
설명서는 프로그램 폴더 안 `manual/` 에 두고, 코드와 **같은 저장**에 넣는다.

## 형식 -- HTML 슬라이드
| 규칙 | 내용 |
|---|---|
| 웹페이지 | `manual/index.html`(목차) + 기능마다 `manual/<기능>.html`. 브라우저로 열면 바로 읽힌다 |
| 페이지 = 슬라이드 | `<section class="slide">` 하나 = 16:9 한 페이지 = pptx 한 장. `P` 발표 모드, 인쇄 = 16:9 PDF |
| 글보다 그림 | 페이지마다 순서도·화면 흉내·카드·비교·표 중 하나. 글은 제목 한 줄 + 짧은 설명 한 줄 |
| 한 단계 = 한 페이지 | 시작하기(`start-step`)·따라 하기(`step`)는 단계마다 페이지를 나눈다 |
| 강의 대본 | 페이지마다 `<aside class="notes">` 3~4문장(60자 이상), 말하듯이. 유튜브 영상 녹화 때 그대로 읽는다 (`N` 키로 보기) |
| 단계 = 화면 | 따라 하기·시작하기 페이지에는 **화면 흉내(`.screen`)나 실제 캡처(`img`)** -- 누르는 곳을 `.hl` 로 |
| 쉬운 말 | 개념과 실사용만. 코드·파일 구조·전문 용어는 쓰지 않는다 |
| 오프라인 | 이 스킬의 `assets/manual.css`, `assets/manual.js` 를 `manual/assets/` 에 복사해서 쓴다 (인터넷 주소 금지) |

## 언제 만들고 고치나
작업 카드(light-start)의 `설명서:` 줄에 미리 정하고, 저장 전에 다시 확인한다.

| 이번 작업 | 설명서 | 할 일 |
|---|---|---|
| 처음 만듦 (첫 기능 / 새 사이트) | **새로 만듦** | `templates/index.html` + 기능마다 `templates/chapter.html` |
| 새 기능 추가 | **장 추가** | `manual/<기능>.html` + 목차 기능 카드 + 바뀐 점 + 모든 장의 `data-chapters` |
| 화면 흐름·사용 방법 변경 | **고침** | 바뀐 단계 페이지 + 바뀐 점 |
| 버그 수정(사용법 같음)·정리·색만 바꿈 | **해당 없음** | 카드에 이유 한 줄 |
| 데이터 설계만 (`data-light`, 화면은 아직) | **해당 없음** | "화면이 생기는 다음 단계에서" -- 데이터 설계는 `data/out/spec.md`·`erd.md` 가 설명서다 |
| 저장하는 곳이 생기거나 바뀜 (브라우저에 저장 시작, DB 를 만듦, Supabase 로 옮김, 엑셀 가져오기) | **고침** | 목차에 "데이터는 어디에" 한 페이지 (아래) |

**데이터는 어디에** (목차, `concept` 페이지): 쓰는 사람이 알아야 할 것만 그림으로 -- 내 데이터가 어디에 저장되나(이 기기의 브라우저 /
이 컴퓨터의 `data/app.db` / 인터넷의 Supabase), 백업은 어떻게 하나(브라우저 저장이면 내보내기·불러오기), 지우면 되돌릴 수 있나(`.callout.bad` --
브라우저 저장은 다른 기기·다른 브라우저에서 안 보이고 사이트 데이터를 지우면 사라진다), 누가 볼 수 있나(로그인).
표 이름·칸·SQL 같은 개발 내용은 쓰지 않는다.

## 완성도 기준 (검사가 잡는 바닥선 -- 목표가 아님)
| 무엇 | 기준 |
|---|---|
| 쪽수 | 목차 6쪽 이상 (표지·한눈에·시작하기 단계별·기능 목록·바뀐 점), 기능 장 5쪽 이상 (표지·흐름·단계마다 한 쪽) |
| 순서도 | 칸마다 짧은 설명(`<span>`) -- 제목만 있는 상자 금지 |
| 단계 페이지 | 화면 흉내·캡처 + "이렇게 보이면 성공" 한 줄 |
| 그려 보기 | `manual_tool.py review` -- 모든 페이지를 그려 글 넘침을 찾고, 미리보기 그림을 **직접 눈으로** 본다 |
예시 전체(26쪽): 저장소 `examples/my-homepage/manual/`. 새 설명서는 이 예시만큼 그림이 많아야 한다.

## 만드는 법
1. 처음이면 이 스킬 폴더의 `assets/` 를 `manual/assets/` 로 복사하고, `templates/index.html` 을 `manual/index.html` 로 복사한다.
   기능마다 `templates/chapter.html` 을 `manual/<기능>.html` 로 복사한다 (파일 이름은 기능 지도와 같은 영문).
2. `{{project}}`, `{{feature}}` 와 `<!-- 작성: ... -->` 를 모두 채운다. 단계가 더 있으면 `step` 페이지를 복사해 늘린다.
3. 모든 파일의 `<body data-chapters="index.html|처음;memo.html|메모;...">` 를 같은 목록으로 맞춘다 (위쪽 메뉴).
4. 목차 `changes` 페이지의 표 맨 위에 `날짜 · 무엇이 바뀌었나` 한 줄.

페이지 예시 (따라 하기 한 단계):
```html
<section class="slide" data-kind="step" data-title="2단계. 적고 Enter">
  <p class="kicker">따라 하기 · 2 / 4</p>
  <h2>적고 <kbd>Enter</kbd> → 목록 맨 위에 추가</h2>
  <div class="body">
    <div class="screen">
      <div class="top"><span class="t">나의 홈</span><span class="tab on">할 일</span></div>
      <div class="in"><div class="field hl">할 일을 적고 Enter</div><div class="item">☐ 장보기 <span>×</span></div></div>
    </div>
  </div>
  <aside class="notes">두 번째 단계. 할 일을 적고 엔터를 누르면 목록 맨 위에 추가됩니다.</aside>
</section>
```
그림 부품: `ol.flow`(순서도, `li.on` 강조) · `.screen`(화면 흉내, `.hl` 누르는 곳) · `.cards`/`a.card` · `.compare`(좋음/주의) ·
`table.grid` · `.callout`(한 줄 설명, `.bad` 되돌릴 수 없음) · `<span class="ui">버튼</span>` · `<kbd>Enter</kbd>`.
예시 전체: 저장소 `examples/my-homepage/manual/`.

쓰는 원칙: 버튼·메뉴는 화면 글자 그대로, 한 단계에 한 동작, 제목에 결과까지 ("[추가] → 목록 맨 위에 생깁니다"),
되돌릴 수 없는 것은 `.callout.bad` 로 미리 알린다. 고칠 때는 바뀐 페이지만 고친다.

## 확인 (light-check 와 함께)
1. 바뀐 `step` 페이지를 **실제로 그대로 해 본다**. 설명과 화면이 다르면 코드나 설명서를 고친다.
2. 파이썬이 있으면: `python <이 스킬 폴더>/manual_tool.py check manual/` -- 그림·대본·단계 페이지·쪽수·링크를 점검한다.
3. `python <이 스킬 폴더>/manual_tool.py review manual/` -- 모든 페이지를 그려 넘침을 찾고 `.manual-review/*.png` 미리보기를 만든다.
   **미리보기를 직접 열어** 겹침·넘침·빈 페이지가 없는지 보고, 채팅에도 보여 준다. 고치면 다시 review.
   (playwright 가 없으면 `manual/index.html` 을 브라우저로 열어 페이지마다 눈으로 본다)
4. 기능 지도의 `설명서` 칸에 장 파일을 적는다 (칸이 없으면 표에 칸을 더한다).

## 저장 후 채팅에 보여 주기
저장(light-save)이 끝나면 바뀐 장의 **페이지별 제목과 핵심 한 줄**을 채팅에 보여 준다
(파이썬이 있으면 `python <스킬 폴더>/manual_tool.py outline manual/ <장>` 출력을 그대로).
맨 끝에: `열기: manual/index.html (브라우저 · P = 발표 · N = 대본)`. 파일을 보낼 수 있는 환경이면 파일도 함께 보낸다.

사용자가 "사용설명서 보여 줘" → 목차 개요, "○○ 설명 보여 줘" → 그 장 개요.
"pptx 로 만들어 줘" → `python <스킬 폴더>/manual_tool.py pptx manual/ 사용설명서.pptx`
(필요하면 `pip install playwright python-pptx && python -m playwright install chromium`). 안 되면 브라우저 인쇄로 16:9 PDF.

## 하지 않는 것
- 글만 있는 페이지, 여러 단계를 한 페이지에 몰아넣기, 대본 없는 페이지, 제목만 있는 순서도 상자
- 템플릿 쪽수 그대로 끝내기, 그려 보지 않고 "다 됐다" 하기
- 확인하지 않은 단계를 쓰기, 개발자용 내용(파일 구조·함수) 쓰기 -- 그건 `dev-notes/` 에 있다
- 새 기능·사용법 변경인데 설명서 없이 "기능:" 으로 저장하기
