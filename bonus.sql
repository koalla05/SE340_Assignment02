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

--- If Thread A locks Row 1 and wants Row 2, while Thread B locks Row 2 and wants Row 1, a Deadlock occurs.

-- FIX 1: Eliminate Row-Level Blocking on Customers using SKIP LOCKED
-- Instead of blindly hammering a single customer record, threads can check
-- if a row is safe to access, skipping it if another worker is updating it.
BEGIN;
SELECT customer_id
FROM customers
WHERE customer_id = 1
    FOR UPDATE SKIP LOCKED;
-- If another PID has it, this returns 0 rows instantly instead of freezing!
COMMIT;


-- FIX 2: Replace Broad Table Locks with Targeted Row-Level Logic
-- Never use 'LOCK TABLE orders IN SHARE ROW EXCLUSIVE MODE;' for simple operations.
-- If an operation needs to guarantee safety, use row-level explicit locking:
BEGIN;
SELECT order_id
FROM orders
WHERE order_id = 19693
    FOR UPDATE; -- Blocks ONLY order 19693, allowing other orders to update in parallel!

UPDATE orders
SET status = 'paid'
WHERE order_id = 19693;
COMMIT;