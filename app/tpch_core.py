"""
tpch_core.py - Demo tren TPC-H (benchmark ma paper 5.5 lambda-Tune dung)
- Sinh du lieu TPC-H (SF 0.05 ~ 50MB) bang tpchgen-cli (cong cu sinh du lieu chuan TPC-H, cai qua pip)
- Nap vao MySQL database `tpch` (chi co PRIMARY KEY, chua co index phu -> "chua toi uu")
- Kich ban D-Bot: truy van Q17 (truy van con tuong quan) + AI trơn vs AI kieu D-Bot
- Mini lambda-Tune: workload 5 truy van TPC-H, AI de xuat cau hinh, chay that co timeout

Chay doc lap (khong can giao dien):
    python tpch_core.py --password <mat_khau_root> [--sf 0.1]
"""
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time

import pymysql

import demo_core as core

TDB = "tpch"

# =====================================================================
# 1. LUOC DO TPC-H (8 bang) - chi PRIMARY KEY, KHONG co index phu
# =====================================================================
SCHEMA = {
    "region": """CREATE TABLE region (r_regionkey INT PRIMARY KEY, r_name CHAR(25), r_comment VARCHAR(152))""",
    "nation": """CREATE TABLE nation (n_nationkey INT PRIMARY KEY, n_name CHAR(25), n_regionkey INT,
                 n_comment VARCHAR(152))""",
    "supplier": """CREATE TABLE supplier (s_suppkey INT PRIMARY KEY, s_name CHAR(25), s_address VARCHAR(40),
                 s_nationkey INT, s_phone CHAR(15), s_acctbal DECIMAL(15,2), s_comment VARCHAR(101))""",
    "customer": """CREATE TABLE customer (c_custkey INT PRIMARY KEY, c_name VARCHAR(25), c_address VARCHAR(40),
                 c_nationkey INT, c_phone CHAR(15), c_acctbal DECIMAL(15,2), c_mktsegment CHAR(10),
                 c_comment VARCHAR(117))""",
    "part": """CREATE TABLE part (p_partkey INT PRIMARY KEY, p_name VARCHAR(55), p_mfgr CHAR(25), p_brand CHAR(10),
                 p_type VARCHAR(25), p_size INT, p_container CHAR(10), p_retailprice DECIMAL(15,2),
                 p_comment VARCHAR(23))""",
    "partsupp": """CREATE TABLE partsupp (ps_partkey INT, ps_suppkey INT, ps_availqty INT,
                 ps_supplycost DECIMAL(15,2), ps_comment VARCHAR(199), PRIMARY KEY (ps_partkey, ps_suppkey))""",
    "orders": """CREATE TABLE orders (o_orderkey INT PRIMARY KEY, o_custkey INT, o_orderstatus CHAR(1),
                 o_totalprice DECIMAL(15,2), o_orderdate DATE, o_orderpriority CHAR(15), o_clerk CHAR(15),
                 o_shippriority INT, o_comment VARCHAR(79))""",
    "lineitem": """CREATE TABLE lineitem (l_orderkey INT, l_partkey INT, l_suppkey INT, l_linenumber INT,
                 l_quantity DECIMAL(15,2), l_extendedprice DECIMAL(15,2), l_discount DECIMAL(15,2),
                 l_tax DECIMAL(15,2), l_returnflag CHAR(1), l_linestatus CHAR(1), l_shipdate DATE,
                 l_commitdate DATE, l_receiptdate DATE, l_shipinstruct CHAR(25), l_shipmode CHAR(10),
                 l_comment VARCHAR(44), PRIMARY KEY (l_orderkey, l_linenumber))""",
}
TABLES = list(SCHEMA)


def tconnect(cfg, db=TDB, autocommit=True, local_infile=False):
    return pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"], password=cfg["password"],
                           database=db, autocommit=autocommit, charset="utf8mb4", local_infile=local_infile,
                           cursorclass=pymysql.cursors.DictCursor)


def db_exists(cfg):
    conn = tconnect(cfg, db=None)
    try:
        return bool(core.query(conn, "SHOW DATABASES LIKE %s", (TDB,)))
    finally:
        conn.close()


def table_counts(cfg):
    conn = tconnect(cfg)
    try:
        return {t: core.query(conn, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in TABLES}
    finally:
        conn.close()


# =====================================================================
# 2. SINH DU LIEU + NAP VAO MYSQL
# =====================================================================
def _find_tpchgen():
    exe = "tpchgen-cli.exe" if os.name == "nt" else "tpchgen-cli"
    for p in (shutil.which("tpchgen-cli"), os.path.join(sysconfig.get_path("scripts"), exe),
              os.path.join(sysconfig.get_path("scripts", f"{os.name}_user"), exe)):
        if p and os.path.exists(p):
            return p
    raise RuntimeError("Chua cai tpchgen-cli. Chay lai 1_cai_dat.bat (hoac: python -m pip install tpchgen-cli)")


def generate_csv(sf, out_dir):
    """Sinh 8 file CSV TPC-H o scale factor sf (0.1 ~ 100MB, khoang 2 giay)."""
    subprocess.run([_find_tpchgen(), "csv", "-s", str(sf), "--output-dir", out_dir],
                   check=True, capture_output=True, text=True)
    return {t: os.path.join(out_dir, f"{t}.csv") for t in TABLES}


def load_mysql(cfg, files, log=print):
    """Tao database tpch va nap CSV bang LOAD DATA LOCAL INFILE."""
    conn = tconnect(cfg, db=None, local_infile=True)
    try:
        q = lambda s: core.query(conn, s)
        q(f"DROP DATABASE IF EXISTS {TDB}")
        q(f"CREATE DATABASE {TDB}")
        q(f"USE {TDB}")
        q("SET GLOBAL local_infile = 1")          # MySQL 8 tat mac dinh, can quyen root
        for t in TABLES:
            q(SCHEMA[t])
            t0 = time.perf_counter()
            path = files[t].replace("\\", "/")
            q(f"""LOAD DATA LOCAL INFILE '{path}' INTO TABLE {t}
                  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' LINES TERMINATED BY '\\n'
                  IGNORE 1 LINES""")
            log(f"  {t}: {core.query(conn, f'SELECT COUNT(*) AS n FROM {t}')[0]['n']:,} dong "
                f"({time.perf_counter() - t0:.1f}s)")
        q("ANALYZE TABLE " + ", ".join(TABLES))
    finally:
        conn.close()


def setup_tpch(cfg, sf=0.05, log=print):
    with tempfile.TemporaryDirectory(prefix="tpch_") as d:
        log(f"Sinh du lieu TPC-H SF {sf} ...")
        files = generate_csv(sf, d)
        log("Nap vao MySQL ...")
        load_mysql(cfg, files, log)
    return table_counts(cfg)


# =====================================================================
# 3. KICH BAN D-BOT TREN TPC-H: Q17 (truy van con tuong quan)
# =====================================================================
Q17_SLOW = """SELECT SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem, part
WHERE p_partkey = l_partkey
  AND p_brand = 'Brand#23' AND p_container = 'MED BOX'
  AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem
                    WHERE l_partkey = p_partkey)"""

# Viet lai kieu unnesting (Ch.19 slide 17): tinh AVG cho moi part 1 lan roi JOIN
Q17_FAST = """SELECT SUM(l.l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem l
JOIN part p ON p.p_partkey = l.l_partkey
JOIN (SELECT l_partkey, 0.2 * AVG(l_quantity) AS lim
      FROM lineitem GROUP BY l_partkey) a ON a.l_partkey = l.l_partkey
WHERE p.p_brand = 'Brand#23' AND p.p_container = 'MED BOX'
  AND l.l_quantity < a.lim"""

Q17_INDEX = "CREATE INDEX idx_l_partkey ON lineitem(l_partkey)"

# Cham tu dong: dung nguyen nhan = nhac truy van con tuong quan (hoac cot l_partkey); co cach sua = JOIN/GROUP BY/index
core.GRADING["tpch"] = [r"subquery|truy van con|truy vấn con|tuong quan|tương quan|correlat|dependent|l_partkey",
                        r"join|group by|unnest|derived|index|chi muc|chỉ mục"]


def drop_secondary_indexes(conn):
    rows = core.query(conn, """SELECT DISTINCT table_name AS t, index_name AS i FROM information_schema.statistics
                               WHERE table_schema = %s AND index_name <> 'PRIMARY'""", (TDB,))
    for r in rows:
        core.query(conn, f"DROP INDEX `{r['i']}` ON `{r['t']}`")
    return [r["i"] for r in rows]


def q17_run(cfg, sql, timeout_s=120):
    """Chay Q17, tra ve (giay, ket_qua, EXPLAIN). Co timeout de khong treo lop hoc."""
    conn = tconnect(cfg)
    try:
        hinted = sql.replace("SELECT", f"SELECT /*+ MAX_EXECUTION_TIME({timeout_s * 1000}) */", 1)
        try:
            t, rows = core.timed(conn, hinted)
            val = rows[0]["avg_yearly"]
        except pymysql.err.OperationalError:
            t, val = float(timeout_s), f"> {timeout_s}s (timeout)"
        return t, val, core.explain(conn, sql)
    finally:
        conn.close()


def q17_reset(cfg):
    conn = tconnect(cfg)
    try:
        drop_secondary_indexes(conn)
    finally:
        conn.close()


def q17_add_index(cfg):
    conn = tconnect(cfg)
    try:
        t, _ = core.timed(conn, Q17_INDEX)
        return t
    finally:
        conn.close()


# =====================================================================
# 4. MINI lambda-TUNE TREN TPC-H
# =====================================================================
# 5 truy van TPC-H co nhieu phep JOIN (Q3, Q5, Q10, Q12, Q17), {to} = timeout ms
TPCH_WORKLOAD = [
    ("Q3", """SELECT /*+ MAX_EXECUTION_TIME({to}) */ l_orderkey, SUM(l_extendedprice*(1-l_discount)) AS revenue,
              o_orderdate, o_shippriority
              FROM customer, orders, lineitem
              WHERE c_mktsegment = 'BUILDING' AND c_custkey = o_custkey AND l_orderkey = o_orderkey
                AND o_orderdate < DATE '1995-03-15' AND l_shipdate > DATE '1995-03-15'
              GROUP BY l_orderkey, o_orderdate, o_shippriority
              ORDER BY revenue DESC, o_orderdate LIMIT 10"""),
    ("Q5", """SELECT /*+ MAX_EXECUTION_TIME({to}) */ n_name, SUM(l_extendedprice*(1-l_discount)) AS revenue
              FROM customer, orders, lineitem, supplier, nation, region
              WHERE c_custkey = o_custkey AND l_orderkey = o_orderkey AND l_suppkey = s_suppkey
                AND c_nationkey = s_nationkey AND s_nationkey = n_nationkey AND n_regionkey = r_regionkey
                AND r_name = 'ASIA' AND o_orderdate >= DATE '1994-01-01' AND o_orderdate < DATE '1995-01-01'
              GROUP BY n_name ORDER BY revenue DESC"""),
    ("Q10", """SELECT /*+ MAX_EXECUTION_TIME({to}) */ c_custkey, c_name, SUM(l_extendedprice*(1-l_discount)) AS revenue,
               c_acctbal, n_name
               FROM customer, orders, lineitem, nation
               WHERE c_custkey = o_custkey AND l_orderkey = o_orderkey
                 AND o_orderdate >= DATE '1993-10-01' AND o_orderdate < DATE '1994-01-01'
                 AND l_returnflag = 'R' AND c_nationkey = n_nationkey
               GROUP BY c_custkey, c_name, c_acctbal, n_name ORDER BY revenue DESC LIMIT 20"""),
    ("Q12", """SELECT /*+ MAX_EXECUTION_TIME({to}) */ l_shipmode,
               SUM(CASE WHEN o_orderpriority IN ('1-URGENT','2-HIGH') THEN 1 ELSE 0 END) AS high_line_count
               FROM orders, lineitem
               WHERE o_orderkey = l_orderkey AND l_shipmode IN ('MAIL','SHIP')
                 AND l_commitdate < l_receiptdate AND l_shipdate < l_commitdate
                 AND l_receiptdate >= DATE '1994-01-01' AND l_receiptdate < DATE '1995-01-01'
               GROUP BY l_shipmode ORDER BY l_shipmode"""),
    ("Q17", Q17_SLOW.replace("SELECT", "SELECT /*+ MAX_EXECUTION_TIME({to}) */", 1)),
]

# Prompt theo mau Listing 1 cua lambda-Tune: chi mo ta cac phep JOIN dang "A: B, C" (workload da nen)
TPCH_PROMPT = """Recommend some configuration parameters for MySQL 8.0 to optimize the system's performance.
Parameters might include system-level configurations, like memory, query optimizer or physical design configurations, like index recommendations.
Each row in the following list has the following format:
{a join key A}:{all the joins with A in the workload}
customer.c_custkey: orders.o_custkey
orders.o_orderkey: lineitem.l_orderkey
lineitem.l_suppkey: supplier.s_suppkey
lineitem.l_partkey: part.p_partkey
customer.c_nationkey: nation.n_nationkey, supplier.s_nationkey
nation.n_regionkey: region.r_regionkey
Table sizes: lineitem {li} rows; orders {od} rows; customer {cu} rows; part {pa} rows; supplier {su} rows.
Existing indexes: only primary keys.
The workload runs on a system with the following specs:
memory: {mem}
cores: {cores}
Return ONLY executable SQL statements, one per line, each ending with ';'.
Allowed statement types: SET GLOBAL <variable> = <value>; and CREATE INDEX <name> ON <table>(<columns>);"""

# 3 cau hinh mau de doi chieu
TPCH_PRESETS = {
    "C1 tăng buffer pool": ["SET GLOBAL innodb_buffer_pool_size = 1073741824"],
    "C2 index ngày tháng": ["CREATE INDEX idx_o_date ON orders(o_orderdate)",
                            "CREATE INDEX idx_l_ship ON lineitem(l_shipdate)"],
    "C3 index khóa JOIN": ["CREATE INDEX idx_o_cust ON orders(o_custkey)",
                           "CREATE INDEX idx_l_part ON lineitem(l_partkey)",
                           "CREATE INDEX idx_l_supp ON lineitem(l_suppkey)",
                           "CREATE INDEX idx_c_nation ON customer(c_nationkey)"],
}

TPCH_SAFE = re.compile(r"^(SET\s+GLOBAL\s+(innodb_buffer_pool_size|sort_buffer_size|join_buffer_size|read_buffer_size|"
                       r"read_rnd_buffer_size|tmp_table_size|max_heap_table_size|innodb_io_capacity)\s*=\s*[\w.*]+"
                       r"|CREATE\s+INDEX\s+\w+\s+ON\s+(lineitem|orders|customer|part|partsupp|supplier|nation|region)"
                       r"\s*\([\w\s,]+\))$", re.I)


def parse_config(text):
    text = re.sub(r"```\w*", "", text or "")
    text = re.sub(r"_\(Model .*?\)_", "", text)
    ok, rejected = [], []
    for stmt in text.split(";"):
        s = " ".join(l.strip() for l in stmt.strip().splitlines() if l.strip() and not l.strip().startswith("--")).strip()
        if s:
            (ok if TPCH_SAFE.match(s) else rejected).append(s)
    return ok, rejected


def tpch_prompt(cfg, mem, cores):
    c = table_counts(cfg)
    return (TPCH_PROMPT.replace("{li}", f"{c['lineitem']:,}").replace("{od}", f"{c['orders']:,}")
            .replace("{cu}", f"{c['customer']:,}").replace("{pa}", f"{c['part']:,}")
            .replace("{su}", f"{c['supplier']:,}").replace("{mem}", mem).replace("{cores}", cores))


def reset_config(conn):
    drop_secondary_indexes(conn)
    core.query(conn, "SET GLOBAL innodb_buffer_pool_size = 134217728")
    for v, d in (("sort_buffer_size", 262144), ("join_buffer_size", 262144), ("read_buffer_size", 131072),
                 ("read_rnd_buffer_size", 262144), ("tmp_table_size", 16777216), ("max_heap_table_size", 16777216),
                 ("innodb_io_capacity", 200)):
        core.query(conn, f"SET GLOBAL {v} = {d}")


def run_config(cfg, name, statements, timeout_ms=15000):
    """Ap dung 1 cau hinh, chay 5 truy van TPC-H (moi truy van co timeout), tra ve ket qua."""
    conn = tconnect(cfg)
    try:
        reset_config(conn)
        t_apply, applied, errors = 0.0, [], []
        for s in statements:
            s = core.normalize(s)
            try:
                dt, _ = core.timed(conn, s)
                t_apply += dt
                applied.append(s)
            except pymysql.err.MySQLError as e:
                errors.append(f"{s} -> {e}")
        total, detail, n_to = 0.0, {}, 0
        for qname, q in TPCH_WORKLOAD:
            try:
                dt, _ = core.timed(conn, q.format(to=timeout_ms))
            except pymysql.err.OperationalError:     # 3024 = vuot MAX_EXECUTION_TIME
                dt, n_to = timeout_ms / 1000, n_to + 1
            total += dt
            detail[qname] = round(dt, 2)
        return {"cau_hinh": name, "giay_workload": round(total, 2), "giay_ap_dung": round(t_apply, 1),
                "so_timeout": n_to, **detail, "lenh": applied, "loi": errors}
    finally:
        conn.close()


def cleanup(cfg):
    conn = tconnect(cfg)
    try:
        reset_config(conn)
    finally:
        conn.close()


# =====================================================================
# 5. CHAY DOC LAP
# =====================================================================
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Tao database TPC-H trong MySQL cho demo")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", default="3306")
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default=os.getenv("MYSQL_PWD", ""))
    ap.add_argument("--sf", type=float, default=0.05, help="scale factor: 0.05 ~ 50MB (khuyen dung, Q17 cham ~10s); 0.1 thi Q17 ~1 phut")
    ap.add_argument("--test", action="store_true", help="tao xong thi do thu Q17 va mini lambda-Tune")
    a = ap.parse_args()
    cfg = {"host": a.host, "port": a.port, "user": a.user, "password": a.password}
    t0 = time.perf_counter()
    print(setup_tpch(cfg, a.sf))
    print(f"Xong sau {time.perf_counter() - t0:.0f} giay")
    if a.test:
        q17_reset(cfg)
        print("Q17 cham :", q17_run(cfg, Q17_SLOW)[:2])
        print("Q17 viet lai:", q17_run(cfg, Q17_FAST)[:2])
        print("Tao index:", q17_add_index(cfg))
        print("Q17 + index:", q17_run(cfg, Q17_SLOW)[:2])
        for n, s in {"C0 mặc định": [], **TPCH_PRESETS}.items():
            r = run_config(cfg, n, s)
            print({k: v for k, v in r.items() if k not in ("lenh",)})
        cleanup(cfg)
