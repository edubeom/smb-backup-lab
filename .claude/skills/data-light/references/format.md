# 쉬운 정본 형식 — `data-light/1`

파일: `data/model.light.json` (UTF-8 JSON)

```json
{
  "format": "data-light/1",
  "project": "반찬가게 주문",
  "db": "sqlite",
  "product": {"kinds": ["manage", "analyze"], "main": "manage", "said": "주문을 적어 두고 찾아보고 싶어요. 손님별 금액도요"},
  "goals": [ { 성공 기준 }, ... ],
  "not_doing": ["결제", "배송 추적"],
  "tables": [ { 테이블 }, ... ]
}
```

| 키 | 뜻 |
|---|---|
| `db` | `sqlite`(기본) 또는 `postgresql` (Supabase 포함) |
| `signup` | (Supabase) `open` 누구나 가입 · `closed` 새 가입을 끔. 표의 `access: members` 는 `closed` 일 때만 |
| `outside` | 정본에 일부러 넣지 않은 DB 표 이름 목록 `["log"]` -- `verify` 의 데이터 보호 검사에서 뺀다 |

## 만드는 것 `product` — 맨 먼저, 묻고 짐작하지 않는다
이 데이터 작업으로 **무엇을 만들어 넘기는지**. 설계 파일이 다 맞아도 "무엇을 만들었나"를 판정할 기준이 없으면 끝이 아니다.

| 키 | 뜻 |
|---|---|
| `kinds` | 하나 이상: `design` 설계까지(SQL·그림·정의서) · `load` 파일·엑셀 넣기 · `manage` 데이터 관리(사람이 찾아보고 고침) · `analyze` 알고 싶은 것 보기(분석) |
| `main` | 여러 개면 그중 주 목적 하나 (하나면 생략) |
| `said` | **사용자가 한 말 그대로** -- Claude 가 첨부·대화에서 짐작한 것은 적지 않는다 |
| `main_said` | 여럿일 때 주 목적에 대해 사용자가 한 말 그대로 (있으면 `upgrade --facts` 가 전문판 `data.product_primary` 로 옮기고, 없으면 전문판에서 다시 묻는다) |

- 없으면 `check` 는 참고로 알리고, `verify` 는 **유형 미정**으로 실패한다.
- `analyze` 가 들어 있으면 성공 기준(`goals`)이 하나 이상 있어야 `verify` 가 통과한다 (알고 싶은 것이 실제로 나오는지 봐야 하므로).
- `manage` 의 화면(찾고 고치기)은 `verify` 가 보지 않는다 -- 사용자와 함께 화면에서 확인한다.
- 전문판 `data.product_type`·`data.product_primary` 의 부분집합이다 (`design`=modeling_files · `load`=ingest_pipeline ·
  `manage`=data_management · `analyze`=analytics_bi). `upgrade --facts data/facts.json` 이 전문판 질문 대장으로 옮긴다 (사용자 답, `said` 가 원문).

## 성공 기준 `goals` — 설계보다 먼저
"이 데이터로 무엇을 알 수 있어야 하나?"를 확인 쿼리로 적는다. `try`·`verify` 가 샘플 데이터로 실제로 돌린다.

| 키 | 뜻 |
|---|---|
| `say` | 한 문장 (`손님별 주문 금액 합계를 볼 수 있다`) |
| `sql` | 조회 한 문장 (SELECT/WITH 만. 데이터를 바꾸는 문장은 `check` 가 막는다). SQLite·PostgreSQL 양쪽에서 도는 표준 SQL 로 |
| `expect` | 생략 → 1행 이상 · 숫자 → 정확한 행 수 · `[[...], ...]` → 정확한 결과(순서 무관) |

```json
{"say": "판매 중인 반찬만 골라 볼 수 있다", "sql": "SELECT dish_name FROM dish WHERE on_sale", "expect": [["멸치볶음"]]}
```

## 하는 일 `works` — 표마다 왜 있고 누가 쓰나
이 데이터를 쓰는 일 3~7개. 표마다 "왜 있나 · 누가 쓰나"가 여기서 나온다 (데이터 설명서 `out/book.html`). 고르는 항목 -- 없어도 검사는 그대로.

| 키 | 뜻 |
|---|---|
| `name` | 무슨 일인지 한 줄 (필수) -- `주문 받기` |
| `who` | 누가 -- `사장님` |
| `say` | 조금 더 설명 |
| `screen` | 어느 화면에서 -- 글자 하나 또는 목록 |
| `uses` | `{표 이름: "CRUD 중 글자"}` -- C 만들기 · R 읽기 · U 고치기 · D 지우기 (`{"orders": "C", "dish": "R"}`) |

- `check`: 없는 표 · 잘못된 글자는 고칠 것, 쓰는 일이 없는 표 · 7개를 넘는 일은 참고 (짝 표 `pi_X` 는 X 의 일을 따른다).
- 전문판으로: `upgrade --blueprint data/blueprint.json` 이 역할(누가) · 화면 · 기능(하는 일)으로 옮긴다 (업무 단계 · 요청 · API 는 전문판에서 사용자와 정리).

## 만들지 않을 것 `not_doing`
이번 범위 밖. 테이블의 `name` 이나 `label` 이 이 목록과 같으면 `check` 가 막는다 — 범위를 넓히려면 사용자와 먼저 정한다.

## 테이블
| 키 | 필수 | 뜻 |
|---|---|---|
| `name` | ✔ | 영어 소문자·숫자·밑줄, 글자로 시작, 예약어 금지. 단수 명사 (`customer`) |
| `label` | 권장 | 한글 이름표 (`손님`) |
| `about` | | 한 줄 설명 |
| `columns` | ✔ | 칸 목록 |
| `sample` | | 확인용 샘플 행 `[{칸: 값}]` — `try` 가 넣어 본다 |
| `timestamps` | | 기본 true → `created_at`·`updated_at` 자동 추가. 끄려면 false |
| `access` | | (Supabase) 이 표를 누가 쓰나: `members` 로그인한 사람 모두(새 가입을 끈 사이트만) · `own` 자기 행만(owner 칸 필요, `pi_` 는 짝 테이블의 owner 를 따름) · `read` 누구나 읽기만(`pi_` 금지). 없으면 규칙 없음 = 공개용 키로 못 씀. **두 개 목록이면 넓은 쪽이 읽기, 좁은 쪽이 쓰기**: `["read", "own"]` 게시판(누구나 읽고, 쓰기·고치기·지우기는 자기 글만), `["read", "members"]` 공지(누구나 읽고 회원이 씀), `["members", "own"]` 회원끼리 보고 자기 것만 고침. `split` 은 짝 표의 access 를 `pi_` 로 옮긴다(read 는 빼고, owner 가 있으면 own) |
| `unique` | | 두 칸 이상을 묶은 중복 금지 `[["member_id", "dues_month"]]` → `UNIQUE (member_id, dues_month)`. 칸 하나면 그 칸에 `"unique": true` |

## 칸
| 키 | 뜻 |
|---|---|
| `name` | 영어 이름 (테이블과 같은 규칙) |
| `label` | 한글 이름표 |
| `type` | 아래 자료형 중 하나 |
| `required` | true 면 비울 수 없음 |
| `unique` | true 면 같은 값 두 번 금지 |
| `choices` | 고를 수 있는 값 목록 (text·int 만) |
| `default` | 기본값 |
| `ref` | (type ref) 가리키는 테이블 |
| `on_delete` | (type ref) 가리키는 행을 지울 때: `keep`(못 지우게, 기본) · `delete`(같이 지움) · `clear`(비움) |
| `key` | (type ref) 이 칸이 키 — 1:1 짝 테이블(`pi_`), 연결 테이블(여러 칸 key) |
| `personal` | 개인정보 종류: `name` `phone` `email` `address` `birth` `id_number` `account` `health` `photo` `location` `other` (또는 true) |
| `about` | 설명 |

## 자료형
| type | 뜻 | SQLite | PostgreSQL |
|---|---|---|---|
| `id` | 자동 번호 (테이블마다 하나, 이름은 `<테이블>_id`) | INTEGER PRIMARY KEY AUTOINCREMENT | bigint identity |
| `ref` | 다른 테이블을 가리킴 (이름 끝 `_id`) | INTEGER + REFERENCES | bigint + REFERENCES |
| `text` | 짧은 글 | TEXT | text |
| `long_text` | 긴 글 | TEXT | text |
| `int` | 정수 | INTEGER | integer |
| `number` | 소수 포함 숫자 | REAL | numeric |
| `money` | 금액 (음수 금지) | NUMERIC | numeric(15,2) |
| `bool` | 예/아니오 | INTEGER 0/1 | boolean |
| `date` | 날짜 `2026-10-14` | TEXT | date |
| `datetime` | 날짜+시각 | TEXT | timestamptz |
| `owner` | 만든 사람(로그인한 계정). 표마다 하나 | TEXT (로그인이 없어 비워 둠) | uuid NOT NULL DEFAULT auth.uid() (Supabase) |

## 규칙 (`check`)
- 테이블마다 행을 구별할 키: `id` 칸 하나, 또는 key 표시한 ref 칸(들).
- ref 는 있는 테이블을, 칸 하나로 된 키를 가진 테이블을 가리킨다.
- `required` 인 ref 끼리 돌고 도는 순환 금지.
- 개인정보(`personal`) 칸은 `pi_` 테이블에만 → `split --write` 가 `pi_<테이블>`(본 테이블과 1:1, 같이 지워짐)로 옮긴다.
- 이름은 영어 소문자·숫자·밑줄, SQL 예약어 금지.

## `verify` 가 보는 것 (통과/실패 관문)
1. `check` — 위 규칙 + 성공 기준 형식
2. `try` — 샘플 넣기, 성공 기준 쿼리 결과, 나쁜 값(빈 값·목록 밖·음수 금액·중복·묶음 중복·없는 대상·지우면 안 되는 행 삭제)이 막히는지
3. `out/` 이 정본과 같은지 (정본을 고치고 build 를 잊으면 실패)
4. 옆에 `app.db` 가 있으면: 데이터가 든 테이블·칸이 정본에서 빠졌는지 (되돌릴 수 없는 변경 → 실패)
5. `--mutate`: 규칙을 하나씩 일부러 빼고 2번이 잡아내는지 (검사 장치 검증)

## 전문판과의 관계
이 형식은 data-expert-framework `model.json` 의 부분집합이다. `upgrade` 가 변환한다:
type → domains, label → terms(한글명), 이름 토큰 → words(한글명은 전문판 공통 사전 대조 때 정리),
ref → fk(on_delete RESTRICT/CASCADE/SET NULL) + relationships, personal → `pi`/`pi_type`, `pi_` 테이블 → `is_pi`,
표의 unique 묶음 → `uniques`, goals → `success_goals`, not_doing → assumptions,
product → (`--facts`) 전문판 질문 대장의 `data.product_type`·`data.product_primary` (source user, quote = said).

## PostgreSQL(Supabase) 로 만들 때
`build --db postgresql` 은 모든 표에 `ENABLE ROW LEVEL SECURITY` 를 넣고, 표의 `access` 대로 실제 규칙(policy)을 만든다.
`access` 가 없는 표는 규칙 없이 틀만 주석으로 단다(정해진 사람끼리는 "새 가입을 끈 경우에만" 이 붙음). 규칙이 없으면 공개용 키로 아무것도 읽을 수 없다.
짝 표에는 access 가 있는데 `pi_` 표에 없으면 `check` 가 "참고"로 알린다 (Supabase 에서 아무도 못 읽음).
`signup: closed` 면 SQL 머리에 Supabase 설정 안내가 붙는다: 새 가입 끄기 · 익명 로그인 끄기 · 계정은 Authentication → Users → Add user.
틀은 `out/schema.sql` 을 고치지 말고 SQL 편집기에서 실행하고, 실행한 것은 `data/supabase-policies.sql` 에 적어 둔다.
