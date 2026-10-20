# 예제 — 반찬가게 주문

손님이 반찬을 주문하는 작은 가게. 손님 · 반찬 · 주문 · 주문 항목, 그리고 손님 개인정보(`pi_customer`).

- 정본: [data/model.light.json](data/model.light.json)
- 그림: [data/out/erd.md](data/out/erd.md) · 정의서: [data/out/spec.md](data/out/spec.md) · SQL: [data/out/schema.sql](data/out/schema.sql)

처음에는 손님 이름·전화번호가 `customer` 에 있었고, `check` 가 알려 줘서 `split --write` 로 `pi_customer` 로 옮겼다.
주문을 지우면 주문 항목도 같이 지워지고(`on_delete: delete`), 주문이 있는 손님·반찬은 지울 수 없다(기본 `keep`).

```bash
python ../../scripts/datalight.py check data/model.light.json
python ../../scripts/datalight.py try   data/model.light.json
python ../../scripts/datalight.py build data/model.light.json
```
