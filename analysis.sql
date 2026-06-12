CREATE EXTENSION pg_stat_statements;

-- Query History --
SELECT
    query,
    calls,
    total_exec_time,
    mean_exec_time,
    rows
FROM pg_stat_statements
ORDER BY total_exec_time DESC
    LIMIT 100;

SHOW config_file;

--- Lock Analysis
SELECT
    a.pid,
    a.usename,
    l.locktype,
    l.mode,
    l.granted,
    a.query
FROM pg_locks l
         JOIN pg_stat_activity a ON l.pid = a.pid
WHERE a.pid <> pg_backend_pid()
ORDER BY a.pid;

-- See Active Locks and Who is Blocking Whom --
SELECT
    blocked.pid AS blocked_pid,
    blocked.query AS blocked_query,
    blocking.pid AS blocking_pid,
    blocking.query AS blocking_query,
    blocked.wait_event_type,
    blocked.wait_event
FROM pg_stat_activity blocked
         JOIN pg_stat_activity blocking ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
ORDER BY blocked.pid;

-- Check for Long-Running Transactions --
SELECT
    pid,
    usename,
    state,
    xact_start,
    now() - xact_start AS transaction_duration,
    query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY xact_start DESC;

-- Analyze specific queries --

EXPLAIN ANALYZE
SELECT *
FROM customers
WHERE email LIKE '%gmail%';
-- SEQ SCAN -> 3.7 ms --

EXPLAIN ANALYZE
SELECT *
FROM orders
WHERE delivery_city LIKE '%a%'
  AND status = 'paid';
-- SEQ SCAN -> 15.8 ms --

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
-- SORT top-N heapsort, HASH Join, SEQ SCAN on orders, customers -> 26.7 ms --

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
-- SORT top-N heapsort, HASHaggregate, Group Key: customer_id, event_type, Seq Scan on customer_events_wide -> 72.8 ms --

EXPLAIN ANALYZE
SELECT
    p.category,
    COUNT(*) AS items_sold,
    SUM(oi.quantity * oi.unit_price) AS revenue
FROM order_items oi
         JOIN products p ON oi.product_id = p.product_id
GROUP BY p.category
ORDER BY revenue DESC;
-- SORT quicksort, GROUP Aggregate Group Key: p.category, Gather merge, quicksort on p.category,
-- Partial HashAggregate, HASH Join,  Parallel Seq Scan on order_items, Seq Scan on products
--- -> 59.1 ms ---

EXPLAIN ANALYZE
SELECT COUNT(*)
FROM customers c
         JOIN orders o ON o.customer_id = c.customer_id
         JOIN customer_events_wide e ON e.customer_id = c.customer_id
WHERE c.status IN ('active', 'inactive')
  AND e.event_time >= NOW() - INTERVAL '90 days';
--- Parallel Hash Join, Parallel Seq Scan on orders, Parallel Hash, Hash Join,
-- Parallel Seq Scan on customer_events_wide, Seq Scan on customers
-- -> 36.8 ms