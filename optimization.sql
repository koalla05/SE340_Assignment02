CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_customers_email_trgm ON customers USING gin (email gin_trgm_ops);

EXPLAIN ANALYZE
SELECT *
FROM customers
WHERE email LIKE '%gmail%';
-- BEFORE: SEQ SCAN -> 3.7 ms --
--- AFTER: Bitmap Heap Scan, Bitmap Index Scan -> 0.02 ms

CREATE INDEX idx_orders_city_partial
    ON orders USING gin (delivery_city gin_trgm_ops)
    WHERE status = 'paid';

EXPLAIN ANALYZE
SELECT *
FROM orders
WHERE delivery_city LIKE '%a%'
  AND status = 'paid';
--- BEFORE: SEQ SCAN -> 15.8 ms ---
--- AFTER: Seq Scan + shared buffers 9.8 ms

CREATE INDEX idx_customers_active ON customers(customer_id) WHERE status = 'active';
CREATE INDEX idx_orders_cust_amount ON orders(customer_id, total_amount);

EXPLAIN ANALYZE
SELECT
    c.customer_id,
    c.full_name,
    COUNT(o.order_id) AS orders_count,
    SUM(o.total_amount) AS revenue
FROM customers c
         JOIN orders o ON c.customer_id = o.customer_id
WHERE c.status = 'active'
GROUP BY c.customer_id, c.full_name
ORDER BY revenue DESC
LIMIT 100;
--- BEFORE: SORT top-N heapsort, HASH Join, SEQ SCAN on orders, customers -> 26.7 ms --
--- AFTER: Index Scan using idx_customers_active on customers -> 27.4 ms ---

CREATE INDEX idx_events_covering_optimized
    ON customer_events_wide (customer_id, event_type, event_time);

VACUUM ANALYZE customer_events_wide; -- to clean up dirty rows

EXPLAIN ANALYZE
SELECT
    customer_id,
    event_type,
    COUNT(*) AS events_count,
    MAX(event_time) AS last_event_time
FROM customer_events_wide
WHERE event_time >= NOW() - INTERVAL '180 days'
GROUP BY customer_id, event_type
ORDER BY events_count DESC
LIMIT 200;
--- BEFORE: SORT top-N heapsort, HASHaggregate, Group Key: customer_id, event_type, Seq Scan on customer_events_wide -> 72.8 ms --
--- AFTER: Index Only Scan -> 35.2 ms

EXPLAIN ANALYZE
SELECT
    p.category,
    COUNT(*) AS items_sold,
    SUM(oi.quantity * oi.unit_price) AS revenue
FROM order_items oi
         JOIN products p ON oi.product_id = p.product_id
GROUP BY p.category
ORDER BY revenue DESC;
-- BEFORE: SORT quicksort, GROUP Aggregate Group Key: p.category, Gather merge, quicksort on p.category,
-- Partial HashAggregate, HASH Join,  Parallel Seq Scan on order_items, Seq Scan on products
--- -> 59.1 ms ---
CREATE INDEX idx_order_items_covering_perf
    ON order_items (product_id, quantity, unit_price);

EXPLAIN ANALYZE
SELECT
    p.category,
    SUM(oi_agg.items_sold) AS items_sold,
    SUM(oi_agg.revenue) AS revenue
FROM (
         -- Aggregate first to reduce rows down to a max of 2,000
         SELECT
             product_id,
             COUNT(*) AS items_sold,
             SUM(quantity * unit_price) AS revenue
         FROM order_items
         GROUP BY product_id
     ) oi_agg
         JOIN products p ON oi_agg.product_id = p.product_id
GROUP BY p.category
ORDER BY revenue DESC;
--- AFTER: HashAggregate, Hash Join, Seq Scan -> 39.2 ms

EXPLAIN ANALYZE
SELECT COUNT(*)
FROM customers c
         JOIN orders o ON o.customer_id = c.customer_id
         JOIN customer_events_wide e ON e.customer_id = c.customer_id
WHERE c.status IN ('active', 'inactive')
  AND e.event_time >= NOW() - INTERVAL '90 days';
--- BEFORE: Parallel Hash Join, Parallel Seq Scan on orders, Parallel Hash, Hash Join,
-- Parallel Seq Scan on customer_events_wide, Seq Scan on customers
-- -> 36.8 ms
VACUUM ANALYZE customer_events_wide;

CREATE INDEX IF NOT EXISTS idx_customers_status_id ON customers (customer_id) WHERE status IN ('active', 'inactive');

CREATE INDEX IF NOT EXISTS idx_orders_customer_id_only ON orders (customer_id);

EXPLAIN ANALYZE
SELECT SUM(o_cnt * e_cnt)
FROM (
         SELECT customer_id, COUNT(*) as o_cnt
         FROM orders GROUP BY customer_id
     ) o
         JOIN (
    SELECT customer_id, COUNT(*) as e_cnt
    FROM customer_events_wide
    WHERE event_time >= NOW() - INTERVAL '90 days' GROUP BY customer_id
) e ON o.customer_id = e.customer_id
         JOIN customers c ON c.customer_id = o.customer_id
WHERE c.status IN ('active', 'inactive');
--- AFTER: Hash join, Merge join, GroupAggregate,
-- Index Only Scan using idx_events_covering_optimized and idx_customers_status_id,
-- Subquery Scan, HashAggregate, Seq Scan -> 38 ms


-- 1. The Core Event Table (100% Append-Only)
-- This table will never experience UPDATEs, making its Visibility Map permanently clean.
CREATE TABLE customer_events_core (
  event_id BIGSERIAL PRIMARY KEY,
  customer_id INT NOT NULL,
  event_type VARCHAR(50) NOT NULL,
  event_time TIMESTAMP NOT NULL
);

-- 2. The Attributes Table (Handles high-frequency UPDATEs)
-- Isolates the background worker updates so they do not bloat our analytical queries.
CREATE TABLE customer_event_attributes (
   event_id BIGINT PRIMARY KEY REFERENCES customer_events_core(event_id) ON DELETE CASCADE,
   attr_01 TEXT,
   attr_02 TEXT,
   attr_03 TEXT
);

-- Migrate existing data --
BEGIN;

-- Populate the Core table
INSERT INTO customer_events_core (customer_id, event_type, event_time)
SELECT customer_id, event_type, event_time
FROM customer_events_wide
ORDER BY event_time ASC;

-- Populate the Attributes table by matching records back
INSERT INTO customer_event_attributes (event_id, attr_01)
SELECT c.event_id, w.attr_01
FROM customer_events_core c
         JOIN customer_events_wide w
              ON c.customer_id = w.customer_id
                  AND c.event_type = w.event_type
                  AND c.event_time = w.event_time;

COMMIT;

CREATE INDEX idx_events_core_time_leading
    ON customer_events_core (event_time, customer_id, event_type);

VACUUM ANALYZE customer_events_core;

EXPLAIN ANALYZE
SELECT
    customer_id,
    event_type,
    COUNT(*) AS events_count,
    MAX(event_time) AS last_event_time
FROM customer_events_core
WHERE event_time >= NOW() - INTERVAL '180 days'
GROUP BY customer_id, event_type
ORDER BY events_count DESC
LIMIT 200;

--- AFTER: Index Oly Scan -> 32 ms