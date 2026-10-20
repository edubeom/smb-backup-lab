-- 반찬가게 주문 -- sqlite schema (data-light)
-- 이 파일은 model.light.json 에서 만들어졌습니다. 직접 고치지 말고 정본을 고친 뒤 다시 build 하세요.
PRAGMA foreign_keys = ON;  -- SQLite 는 이 줄이 있어야 참조 규칙이 동작합니다

-- 손님: 주문하는 손님
CREATE TABLE customer (
  customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
  nickname TEXT NOT NULL UNIQUE,
  grade TEXT NOT NULL DEFAULT '일반' CHECK (grade IN ('일반', '단골')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 손님 개인정보: 손님의 개인정보. 본 테이블과 1:1, 본 행을 지우면 같이 지워진다
CREATE TABLE pi_customer (
  customer_id INTEGER NOT NULL REFERENCES customer (customer_id) ON DELETE CASCADE,
  full_name TEXT NOT NULL,
  phone TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (customer_id)
);

-- 반찬: 파는 반찬
CREATE TABLE dish (
  dish_id INTEGER PRIMARY KEY AUTOINCREMENT,
  dish_name TEXT NOT NULL UNIQUE,
  price NUMERIC NOT NULL CHECK (price >= 0),
  on_sale INTEGER NOT NULL DEFAULT 1 CHECK (on_sale IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 주문: 손님의 주문 한 건
CREATE TABLE orders (
  orders_id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customer (customer_id) ON DELETE RESTRICT,
  order_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('접수', '준비', '완료', '취소')),
  memo TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_orders_customer_id ON orders (customer_id);

-- 주문 항목: 주문 한 건에 담긴 반찬
CREATE TABLE order_item (
  order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  orders_id INTEGER NOT NULL REFERENCES orders (orders_id) ON DELETE CASCADE,
  dish_id INTEGER NOT NULL REFERENCES dish (dish_id) ON DELETE RESTRICT,
  qty INTEGER NOT NULL DEFAULT 1,
  unit_price NUMERIC NOT NULL CHECK (unit_price >= 0),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (orders_id, dish_id)
);
CREATE INDEX ix_order_item_orders_id ON order_item (orders_id);
CREATE INDEX ix_order_item_dish_id ON order_item (dish_id);
