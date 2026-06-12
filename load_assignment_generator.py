"""
PostgreSQL load assignment generator.

Requirements:
    pip install psycopg2-binary faker

Before running:
    1. Start PostgreSQL locally.
    2. Update DB_CONFIG below.
    3. Run:
       python postgres_assignment_load_fixed.py

Behavior:
    - Does NOT recreate/drop TARGET_DB if it already exists.
    - Creates TARGET_DB only when it is missing.
    - Creates tables only when they are missing.
    - Inserts test data only when the main tables are empty.
    - Starts load immediately when DB and data already exist.
    - Generates normal load, row-lock waits, table locks, and intentional deadlocks.
"""

import random
import time
import threading
from typing import Callable

import psycopg2
from psycopg2 import errors
from faker import Faker


DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "",
    "password": "",
    "dbname": "assignment02",
}

TARGET_DB = "student_perf_lab"
CUSTOMERS_COUNT = 20_000
PRODUCTS_COUNT = 2_000
ORDERS_COUNT = 120_000
EVENTS_COUNT = 200_000

HOT_CUSTOMER_IDS = [1, 2, 3, 4, 5]

fake = Faker()


def get_conn(dbname: str = TARGET_DB):
    config = DB_CONFIG.copy()
    config["dbname"] = dbname
    return psycopg2.connect(**config)


def ensure_database() -> None:
    """Create TARGET_DB only if it does not already exist."""
    conn = get_conn("postgres")
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (TARGET_DB,))
            exists = cur.fetchone() is not None

            if exists:
                print(f"Database {TARGET_DB!r} already exists. Skipping create/drop.")
                return

            cur.execute(f'CREATE DATABASE "{TARGET_DB}";')
            print(f"Database {TARGET_DB!r} created.")
    finally:
        conn.close()


def ensure_schema() -> None:
    """Create required tables only if they do not already exist."""
    conn = get_conn()
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            cur.execute("""
                        CREATE TABLE IF NOT EXISTS customers (
                                                                 customer_id SERIAL PRIMARY KEY,
                                                                 full_name TEXT,
                                                                 email TEXT,
                                                                 phone TEXT,
                                                                 city TEXT,
                                                                 country TEXT,
                                                                 created_at TIMESTAMP,
                                                                 status TEXT
                        );

                        CREATE TABLE IF NOT EXISTS products (
                                                                product_id SERIAL PRIMARY KEY,
                                                                product_name TEXT,
                                                                category TEXT,
                                                                price NUMERIC(10, 2),
                            supplier TEXT,
                            created_at TIMESTAMP
                            );

                        CREATE TABLE IF NOT EXISTS orders (
                                                              order_id SERIAL PRIMARY KEY,
                                                              customer_id INT,
                                                              order_date TIMESTAMP,
                                                              status TEXT,
                                                              total_amount NUMERIC(12, 2),
                            payment_method TEXT,
                            delivery_city TEXT
                            );

                        CREATE TABLE IF NOT EXISTS order_items (
                                                                   order_item_id SERIAL PRIMARY KEY,
                                                                   order_id INT,
                                                                   product_id INT,
                                                                   quantity INT,
                                                                   unit_price NUMERIC(10, 2)
                            );

                        CREATE TABLE IF NOT EXISTS customer_events_wide (
                                                                            event_id SERIAL PRIMARY KEY,
                                                                            customer_id INT,
                                                                            event_type TEXT,
                                                                            event_time TIMESTAMP,
                                                                            source TEXT,
                                                                            campaign TEXT,
                                                                            device TEXT,
                                                                            browser TEXT,
                                                                            os TEXT,
                                                                            ip_address TEXT,
                                                                            page_url TEXT,
                                                                            referrer TEXT,
                                                                            utm_source TEXT,
                                                                            utm_medium TEXT,
                                                                            utm_campaign TEXT,
                                                                            attr_01 TEXT,
                                                                            attr_02 TEXT,
                                                                            attr_03 TEXT,
                                                                            attr_04 TEXT,
                                                                            attr_05 TEXT,
                                                                            attr_06 TEXT,
                                                                            attr_07 TEXT,
                                                                            attr_08 TEXT,
                                                                            attr_09 TEXT,
                                                                            attr_10 TEXT
                        );
                        """)

            # Optional indexes: enough to make row selection deterministic, not enough to remove all slowness.
            cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_customer_id ON orders(customer_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_order_items_product_id ON order_items(product_id);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_events_customer_id ON customer_events_wide(customer_id);")

        print("Schema is ready.")
    finally:
        conn.close()


def table_count(table_name: str) -> int:
    conn = get_conn()
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table_name}";')
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def has_seed_data() -> bool:
    return table_count("customers") > 0 and table_count("products") > 0


def insert_data(
        customers_count: int = CUSTOMERS_COUNT,
        products_count: int = PRODUCTS_COUNT,
        orders_count: int = ORDERS_COUNT,
        events_count: int = EVENTS_COUNT,
) -> None:
    conn = get_conn()
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            for _ in range(customers_count):
                cur.execute("""
                            INSERT INTO customers
                                (full_name, email, phone, city, country, created_at, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """, (
                                fake.name(),
                                fake.email(),
                                fake.phone_number(),
                                fake.city(),
                                fake.country(),
                                fake.date_time_between(start_date="-3y", end_date="now"),
                                random.choice(["active", "inactive", "blocked"]),
                            ))

            for _ in range(products_count):
                cur.execute("""
                            INSERT INTO products
                                (product_name, category, price, supplier, created_at)
                            VALUES (%s, %s, %s, %s, %s)
                            """, (
                                fake.word(),
                                random.choice(["electronics", "food", "clothes", "books", "home"]),
                                round(random.uniform(5, 1000), 2),
                                fake.company(),
                                fake.date_time_between(start_date="-3y", end_date="now"),
                            ))

            for order_id in range(1, orders_count + 1):
                customer_id = random.randint(1, customers_count)
                total = round(random.uniform(10, 3000), 2)

                cur.execute("""
                            INSERT INTO orders
                            (customer_id, order_date, status, total_amount, payment_method, delivery_city)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            """, (
                                customer_id,
                                fake.date_time_between(start_date="-2y", end_date="now"),
                                random.choice(["new", "paid", "shipped", "cancelled", "returned"]),
                                total,
                                random.choice(["card", "cash", "bank_transfer"]),
                                fake.city(),
                            ))

                for _ in range(random.randint(1, 5)):
                    cur.execute("""
                                INSERT INTO order_items
                                    (order_id, product_id, quantity, unit_price)
                                VALUES (%s, %s, %s, %s)
                                """, (
                                    order_id,
                                    random.randint(1, products_count),
                                    random.randint(1, 10),
                                    round(random.uniform(5, 1000), 2),
                                ))

            for _ in range(events_count):
                cur.execute("""
                            INSERT INTO customer_events_wide
                            (
                                customer_id, event_type, event_time, source, campaign,
                                device, browser, os, ip_address, page_url, referrer,
                                utm_source, utm_medium, utm_campaign,
                                attr_01, attr_02, attr_03, attr_04, attr_05,
                                attr_06, attr_07, attr_08, attr_09, attr_10
                            )
                            VALUES (
                                       %s, %s, %s, %s, %s,
                                       %s, %s, %s, %s, %s, %s,
                                       %s, %s, %s,
                                       %s, %s, %s, %s, %s,
                                       %s, %s, %s, %s, %s
                                   )
                            """, (
                                random.randint(1, customers_count),
                                random.choice(["page_view", "click", "purchase", "login", "logout"]),
                                fake.date_time_between(start_date="-1y", end_date="now"),
                                random.choice(["google", "facebook", "email", "direct", "tiktok"]),
                                fake.word(),
                                random.choice(["mobile", "desktop", "tablet"]),
                                random.choice(["chrome", "safari", "firefox", "edge"]),
                                random.choice(["ios", "android", "windows", "macos", "linux"]),
                                fake.ipv4(),
                                fake.url(),
                                fake.url(),
                                fake.word(),
                                fake.word(),
                                fake.word(),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                                fake.text(20),
                            ))

        conn.commit()
        print("Seed data inserted.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_query(name: str, sql: str, sleep_after: float = 0.2) -> None:
    conn = get_conn()
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            start = time.time()
            cur.execute(sql)
            try:
                cur.fetchall()
            except psycopg2.ProgrammingError:
                pass

            duration = round(time.time() - start, 3)
            print(f"{name}: {duration}s")

    except Exception as exc:
        print(f"{name} failed: {type(exc).__name__}: {exc}")

    finally:
        conn.close()
        time.sleep(sleep_after)


def slow_queries_worker() -> None:
    queries = [
        ("search_customer_by_email", """
                                     SELECT *
                                     FROM customers
                                     WHERE email LIKE '%gmail%';
                                     """),
        ("orders_by_city_and_status", """
                                      SELECT *
                                      FROM orders
                                      WHERE delivery_city LIKE '%a%'
                                        AND status = 'paid';
                                      """),
        ("heavy_join", """
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
                       """),
        ("events_aggregation", """
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
                               """),
        ("items_products_join", """
                                SELECT
                                    p.category,
                                    COUNT(*) AS items_sold,
                                    SUM(oi.quantity * oi.unit_price) AS revenue
                                FROM order_items oi
                                         JOIN products p ON oi.product_id = p.product_id
                                GROUP BY p.category
                                ORDER BY revenue DESC;
                                """),
        ("cartesian_pressure", """
                               SELECT COUNT(*)
                               FROM customers c
                                        JOIN orders o ON o.customer_id = c.customer_id
                                        JOIN customer_events_wide e ON e.customer_id = c.customer_id
                               WHERE c.status IN ('active', 'inactive')
                                 AND e.event_time >= NOW() - INTERVAL '90 days';
                               """),
    ]

    while True:
        name, sql = random.choice(queries)
        run_query(name, sql)


def update_worker() -> None:
    while True:
        customer_id = random.randint(1, CUSTOMERS_COUNT)

        sql = f"""
        UPDATE customer_events_wide
        SET attr_01 = 'updated_' || NOW()::TEXT,
            attr_02 = 'changed',
            attr_03 = 'changed',
            attr_04 = 'changed'
        WHERE customer_id = {customer_id};
        """

        run_query("wide_table_update", sql, sleep_after=0.5)


def row_lock_holder_worker() -> None:
    """Holds row locks on hot customer rows so other sessions wait."""
    while True:
        conn = get_conn()
        conn.autocommit = False

        try:
            with conn.cursor() as cur:
                hot_id = random.choice(HOT_CUSTOMER_IDS)
                cur.execute("SET lock_timeout = '30s';")
                cur.execute("""
                            UPDATE customers
                            SET status = 'active'
                            WHERE customer_id = %s;
                            """, (hot_id,))

                print(f"row_lock_holder: locked customer_id={hot_id}")
                time.sleep(random.uniform(8, 15))
                conn.commit()
                print(f"row_lock_holder: committed customer_id={hot_id}")

        except Exception as exc:
            print(f"row_lock_holder failed: {type(exc).__name__}: {exc}")
            conn.rollback()

        finally:
            conn.close()

        time.sleep(random.uniform(0.5, 2))


def conflicting_update_worker() -> None:
    """Frequently updates the same hot rows, causing visible lock waits."""
    while True:
        customer_id = random.choice(HOT_CUSTOMER_IDS + [random.randint(1, CUSTOMERS_COUNT)])

        sql = f"""
        SET lock_timeout = '20s';
        UPDATE customers
        SET phone = 'changed_' || NOW()::TEXT
        WHERE customer_id = {customer_id};
        """

        run_query("conflicting_customer_update", sql, sleep_after=0.1)


def table_lock_worker() -> None:
    """Holds a table-level lock that blocks concurrent writes to orders."""
    while True:
        conn = get_conn()
        conn.autocommit = False

        try:
            with conn.cursor() as cur:
                cur.execute("SET lock_timeout = '30s';")
                cur.execute("LOCK TABLE orders IN SHARE ROW EXCLUSIVE MODE;")
                print("table_lock_worker: locked orders table")
                time.sleep(random.uniform(8, 12))
                conn.commit()
                print("table_lock_worker: committed orders table lock")

        except Exception as exc:
            print(f"table_lock_worker failed: {type(exc).__name__}: {exc}")
            conn.rollback()

        finally:
            conn.close()

        time.sleep(random.uniform(3, 6))


def orders_writer_worker() -> None:
    """Writes to orders, often waiting behind table_lock_worker."""
    while True:
        sql = f"""
        SET lock_timeout = '20s';
        UPDATE orders
        SET status = 'paid'
        WHERE order_id = {random.randint(1, ORDERS_COUNT)};
        """
        run_query("orders_writer", sql, sleep_after=0.2)


def deadlock_worker(name: str, first_customer_id: int, second_customer_id: int) -> None:
    """Creates intentional deadlocks by locking two rows in opposite order."""
    while True:
        conn = get_conn()
        conn.autocommit = False

        try:
            with conn.cursor() as cur:
                cur.execute("SET deadlock_timeout = '500ms';")
                cur.execute("SET lock_timeout = '10s';")

                cur.execute("""
                            UPDATE customers
                            SET city = city
                            WHERE customer_id = %s;
                            """, (first_customer_id,))

                print(f"{name}: locked first customer_id={first_customer_id}")
                time.sleep(1)

                cur.execute("""
                            UPDATE customers
                            SET country = country
                            WHERE customer_id = %s;
                            """, (second_customer_id,))

                conn.commit()
                print(f"{name}: committed")

        except errors.DeadlockDetected as exc:
            print(f"{name}: DEADLOCK detected and rolled back: {exc.pgcode}")
            conn.rollback()

        except Exception as exc:
            print(f"{name} failed: {type(exc).__name__}: {exc}")
            conn.rollback()

        finally:
            conn.close()

        time.sleep(random.uniform(1, 3))


def run_load_test() -> None:
    workers: list[Callable[[], None]] = [
        slow_queries_worker,
        slow_queries_worker,
        slow_queries_worker,
        update_worker,
        row_lock_holder_worker,
        row_lock_holder_worker,
        conflicting_update_worker,
        conflicting_update_worker,
        table_lock_worker,
        orders_writer_worker,
        lambda: deadlock_worker("deadlock_a", 1, 2),
        lambda: deadlock_worker("deadlock_b", 2, 1),
        lambda: deadlock_worker("deadlock_c", 3, 4),
        lambda: deadlock_worker("deadlock_d", 4, 3),
    ]

    threads = []

    for worker in workers:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        threads.append(thread)

    print(f"Started {len(threads)} workers. Press Ctrl+C to stop.")

    while True:
        time.sleep(1)


if __name__ == "__main__":
    print("Ensuring database...")
    ensure_database()

    print("Ensuring schema...")
    ensure_schema()

    if has_seed_data():
        print("Seed data already exists. Skipping insert and starting load test.")
    else:
        print("Seed data not found. Inserting test data...")
        insert_data()

    print("Starting load test...")
    run_load_test()
