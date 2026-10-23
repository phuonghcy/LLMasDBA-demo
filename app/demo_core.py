"""
demo_core.py - Logic cho demo "LLM As DBA" (paper 5.2 D-Bot & 5.5 lambda-Tune)
Tach rieng khoi giao dien de de doc va de test.
Moi ham deu dung MySQL 8 (pymysql) va API Claude / Gemini (requests).
"""
import re
import time
import threading
import pymysql
import requests

DB = "demo_dbot"

# =====================================================================
# 1. KET NOI MYSQL
# =====================================================================
def connect(cfg, db=DB, autocommit=True):
    """Mo 1 ket noi MySQL. cfg = dict(host, port, user, password)."""
    return pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"],
                           password=cfg["password"], database=db, autocommit=autocommit,
                           charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)


def query(conn, sql, args=None):
    """Chay 1 cau SQL, tra ve list[dict] (rong neu khong co ket qua)."""
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall() if cur.description else []


def timed(conn, sql, args=None):
    """Chay SQL va do thoi gian (giay)."""
    t0 = time.perf_counter()
    rows = query(conn, sql, args)
    return time.perf_counter() - t0, rows


def db_exists(cfg):
    conn = connect(cfg, db=None)
    try:
        return bool(query(conn, "SHOW DATABASES LIKE %s", (DB,)))
    finally:
        conn.close()


def table_counts(cfg):
    conn = connect(cfg)
    try:
        return {t: query(conn, f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in ("emp", "dept", "emp_small")}
    finally:
        conn.close()


# =====================================================================
# 2. TAO DU LIEU (giong file 00_setup.sql)
# =====================================================================
def setup_database(cfg, n_emp=1_000_000, n_small=5000):
    conn = connect(cfg, db=None)
    try:
        for sql in [
            f"DROP DATABASE IF EXISTS {DB}",
            f"CREATE DATABASE {DB}",
            f"USE {DB}",
            "CREATE TABLE emp (id INT AUTO_INCREMENT PRIMARY KEY, dno INT, salary INT, name VARCHAR(30))",
            "CREATE TABLE dept (dno INT PRIMARY KEY, dname VARCHAR(30), budget INT)",
            f"SET SESSION cte_max_recursion_depth = {max(n_emp, 1000)}",
            f"""INSERT INTO emp (dno, salary, name)
                WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < {n_emp})
                SELECT FLOOR(1 + RAND()*100), FLOOR(20000 + RAND()*80000), CONCAT('E', n) FROM s""",
            """INSERT INTO dept (dno, dname, budget)
                WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 100)
                SELECT n, CONCAT('Dept_', n), FLOOR(100000 + RAND()*900000) FROM s""",
            f"CREATE TABLE emp_small AS SELECT * FROM emp WHERE id <= {n_small}",
            "ANALYZE TABLE emp, dept, emp_small",
        ]:
            query(conn, sql)
    finally:
        conn.close()


def drop_secondary_indexes(conn):
    """Xoa moi index phu tren emp/dept (giu PRIMARY) -> tro ve trang thai 'chua toi uu'."""
    rows = query(conn, """SELECT DISTINCT table_name AS t, index_name AS i FROM information_schema.statistics
                          WHERE table_schema = %s AND table_name IN ('emp','dept') AND index_name <> 'PRIMARY'""", (DB,))
    for r in rows:
        query(conn, f"DROP INDEX `{r['i']}` ON `{r['t']}`")
    return [r["i"] for r in rows]


def explain(conn, sql):
    return query(conn, "EXPLAIN " + sql)


def fmt_rows(rows, cols=None):
    """Dinh dang ket qua thanh text gon de dua vao prompt."""
    if not rows:
        return "(rong)"
    cols = cols or list(rows[0].keys())
    return "\n".join("  " + ", ".join(f"{c}={r.get(c)}" for c in cols) for r in rows)


# =====================================================================
# 3. KICH BAN 1 - MISSING_INDEXES
# =====================================================================
KB1_SQL = "SELECT COUNT(*) AS so_nv, AVG(salary) AS luong_tb FROM emp WHERE dno = %s"
KB1_EXPLAIN = "SELECT COUNT(*), AVG(salary) FROM emp WHERE dno = 42"


def kb1_workload(cfg, n=30):
    """Ung dung goi n truy van loc theo phong ban. Tra ve tong thoi gian (giay)."""
    conn = connect(cfg)
    try:
        t0 = time.perf_counter()
        for d in range(1, n + 1):
            query(conn, KB1_SQL, (d,))
        return time.perf_counter() - t0
    finally:
        conn.close()


def kb1_break(cfg):
    conn = connect(cfg)
    try:
        drop_secondary_indexes(conn)
        query(conn, "CALL sys.ps_truncate_all_tables(FALSE)")
    finally:
        conn.close()


def kb1_tools(cfg):
    """'Cong cu' kieu D-Bot: EXPLAIN + view sys ve truy van quet toan bang."""
    conn = connect(cfg)
    try:
        exp = explain(conn, KB1_EXPLAIN)
        scans = query(conn, """SELECT query, exec_count, total_latency, rows_examined_avg
                               FROM sys.statements_with_full_table_scans
                               WHERE db = %s AND query LIKE 'SELECT%%' AND query NOT LIKE '%%information_schema%%'
                               ORDER BY exec_count DESC LIMIT 3""", (DB,))
        locks = query(conn, "SELECT COUNT(*) AS n FROM sys.innodb_lock_waits")[0]["n"]
        return {"explain": exp, "full_scans": scans, "lock_waits": locks}
    finally:
        conn.close()


def kb1_fix(cfg):
    conn = connect(cfg)
    try:
        t, _ = timed(conn, "CREATE INDEX idx_dno ON emp(dno)")
        return t, explain(conn, KB1_EXPLAIN)
    finally:
        conn.close()


# =====================================================================
# 4. KICH BAN 2 - CORRELATED_SUBQUERY
# =====================================================================
KB2_SLOW = """SELECT COUNT(*) AS so_nv FROM emp_small e
WHERE e.salary > (SELECT AVG(e2.salary) FROM emp_small e2 WHERE e2.dno = e.dno)"""
KB2_FAST = """SELECT COUNT(*) AS so_nv FROM emp_small e
JOIN (SELECT dno, AVG(salary) AS a FROM emp_small GROUP BY dno) d ON e.dno = d.dno
WHERE e.salary > d.a"""


def kb2_run(cfg, sql):
    conn = connect(cfg)
    try:
        t, rows = timed(conn, sql)
        return t, rows[0]["so_nv"], explain(conn, sql)
    finally:
        conn.close()


# =====================================================================
# 5. KICH BAN 3 - LOCK_CONTENTION (3 ket noi: A giu khoa, B bi chan, C quan sat)
# =====================================================================
class LockScenario:
    def __init__(self, cfg, wait_timeout=20):
        self.cfg, self.wait_timeout = cfg, wait_timeout
        self.conn_a = None
        self.b_result = {"status": "chua chay"}
        self.b_thread = None

    def start_a(self):
        """Phien A: START TRANSACTION + UPDATE, KHONG commit -> giu khoa X tren dong id=1."""
        self.conn_a = connect(self.cfg, autocommit=False)
        query(self.conn_a, "START TRANSACTION")
        query(self.conn_a, "UPDATE emp SET salary = salary + 1 WHERE id = 1")
        return query(self.conn_a, "SELECT CONNECTION_ID() AS pid")[0]["pid"]

    def start_b(self):
        """Phien B (chay nen): UPDATE cung dong -> bi chan cho den khi A tha khoa."""
        def run():
            conn = connect(self.cfg)
            try:
                query(conn, f"SET SESSION innodb_lock_wait_timeout = {self.wait_timeout}")
                self.b_result = {"status": "dang cho khoa..."}
                t0 = time.perf_counter()
                try:
                    query(conn, "UPDATE emp SET salary = salary + 2 WHERE id = 1")
                    self.b_result = {"status": "xong", "seconds": time.perf_counter() - t0}
                except pymysql.err.OperationalError as e:
                    self.b_result = {"status": "loi", "seconds": time.perf_counter() - t0, "error": str(e)}
            finally:
                conn.close()
        self.b_thread = threading.Thread(target=run, daemon=True)
        self.b_thread.start()
        time.sleep(1.5)

    def tools(self):
        """Phien C: 'cong cu' sys.innodb_lock_waits."""
        conn = connect(self.cfg)
        try:
            return query(conn, """SELECT wait_age, locked_table, locked_type, waiting_query,
                                         blocking_pid, blocking_query, sql_kill_blocking_connection
                                  FROM sys.innodb_lock_waits""")
        finally:
            conn.close()

    def fix(self):
        """Sua: phien A ROLLBACK -> nha khoa -> B chay xong."""
        if self.conn_a:
            query(self.conn_a, "ROLLBACK")
            self.conn_a.close()
            self.conn_a = None
        if self.b_thread:
            self.b_thread.join(timeout=self.wait_timeout + 5)
        return self.b_result


# =====================================================================
# 6. THE KINH NGHIEM (4 truong name/content/metrics/steps nhu paper 5.2 Muc 4)
# =====================================================================
EXPERIENCE = """[THE KINH NGHIEM 1]
name: missing_index
content: Truy van loc theo cot khong co index phai quet toan bang, lam tang I/O va CPU.
metrics: rows_examined, full_table_scans, EXPLAIN.type
steps: Chay EXPLAIN; neu type = ALL va rows xap xi so dong cua bang thi la nguyen nhan; de xuat CREATE INDEX tren cot trong WHERE.

[THE KINH NGHIEM 2]
name: lock_contention
content: Giao tac giu khoa lau (chua COMMIT) lam cac giao tac khac phai cho.
metrics: sys.innodb_lock_waits
steps: Neu sys.innodb_lock_waits co dong thi la nguyen nhan; xu ly giao tac dang chan (COMMIT/ROLLBACK/KILL) va rut ngan giao tac.

[THE KINH NGHIEM 3]
name: correlated_subquery
content: Truy van con tuong quan bi chay lai cho tung dong cua truy van ngoai.
metrics: EXPLAIN.select_type
steps: Neu EXPLAIN co DEPENDENT SUBQUERY thi la nguyen nhan; viet lai bang JOIN voi bang tam GROUP BY (unnesting)."""

ANSWER_RULE = "Tra loi bang tieng Viet, toi da 150 tu, theo dang: 'Nguyen nhan: ...' roi 'Cach sua: ...' (kem lenh SQL neu co)."


def prompt_a(symptom):
    """Prompt A - 'AI tron': chi co trieu chung (giong baseline LLM+Metrics)."""
    return f"Database MySQL 8 cua toi gap van de sau:\n{symptom}\nNguyen nhan la gi va toi nen sua the nao?\n{ANSWER_RULE}"


def prompt_b(symptom, tool_text):
    """Prompt B - 'AI kieu D-Bot': trieu chung + the kinh nghiem + ket qua cong cu that."""
    return (f"Ban la DBA. Su co: {symptom}\n\n"
            "Hay chan doan nguyen nhan goc theo quy trinh: doi chieu TUNG the kinh nghiem voi ket qua cong cu, "
            "chi ket luan khi co bang chung trong ket qua cong cu.\n\n"
            f"{EXPERIENCE}\n\n[KET QUA CONG CU THAT]\n{tool_text}\n\n{ANSWER_RULE}")


# Tu khoa de cham tu dong (co the sua tay tren giao dien)
GRADING = {
    "kb1": [r"index|chi muc|chỉ mục", r"dno"],
    "kb2": [r"subquery|truy van con|truy vấn con|tuong quan|tương quan|correlat", r"join|group by|unnest|derived|bang tam|bảng tạm"],
    "kb3": [r"lock|khoa|khóa", r"commit|rollback|kill"],
}


def auto_grade(kb, answer):
    """Dung nguyen nhan = khop tat ca nhom tu khoa; Cu the = co lenh SQL."""
    a = (answer or "").lower()
    correct = all(re.search(p, a) for p in GRADING[kb])
    specific = bool(re.search(r"create index|select |rollback|commit|kill |join ", a))
    return correct, specific


# =====================================================================
# 7. GOI AI: CLAUDE / GEMINI (REST, khong can SDK)
# =====================================================================
def _ask_once(provider, api_key, model, prompt, temperature=0.7, timeout=90):
    if provider == "Claude":
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=timeout,
                          headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": model, "max_tokens": 800, "temperature": temperature,
                                "messages": [{"role": "user", "content": prompt}]})
        if r.status_code != 200:
            raise RuntimeError(f"Claude API loi {r.status_code}: {r.text[:300]}")
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    if provider == "Gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = requests.post(url, timeout=timeout, headers={"x-goog-api-key": api_key, "content-type": "application/json"},
                          json={"contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": temperature, "maxOutputTokens": 2048}})
        if r.status_code != 200:
            raise RuntimeError(f"Gemini API loi {r.status_code}: {r.text[:300]}")
        cands = r.json().get("candidates", [])
        return "".join(p.get("text", "") for p in cands[0]["content"].get("parts", [])) if cands else "(Gemini khong tra loi)"
    raise ValueError("provider phai la Claude hoac Gemini")


RETRY_CODES = ("429", "500", "502", "503", "504", "529")
GEMINI_FALLBACK = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]


def ask_ai(provider, api_key, model, prompt, temperature=0.7, timeout=90):
    """Goi AI co tu thu lai khi server qua tai (503/429...), roi doi sang model du phong (Gemini)."""
    models = [model] + ([m for m in GEMINI_FALLBACK if m != model] if provider == "Gemini" else [])
    last = None
    for m in models:
        for wait in (0, 3, 8):
            if wait:
                time.sleep(wait)
            try:
                ans = _ask_once(provider, api_key, m, prompt, temperature, timeout)
                return ans if m == model else f"_(Model {model} dang qua tai - da dung {m})_\n\n{ans}"
            except RuntimeError as e:
                last = e
                if not any(f"loi {c}" in str(e) for c in RETRY_CODES):
                    raise
            except requests.exceptions.RequestException as e:
                last = e
    raise RuntimeError(f"AI dang qua tai, thu lai sau it phut. Chi tiet: {last}")


# =====================================================================
# 8. MINI lambda-TUNE (paper 5.5): AI de xuat cau hinh -> chay that -> chon nhanh nhat
# =====================================================================
LT_WORKLOAD = [
    ("Q1 thong ke 20 phong", "SELECT /*+ MAX_EXECUTION_TIME({to}) */ COUNT(*) AS n FROM "
     "(SELECT dno, COUNT(*), AVG(salary) FROM emp WHERE dno BETWEEN 1 AND 20 GROUP BY dno) q"),
    ("Q2 JOIN emp-dept", "SELECT /*+ MAX_EXECUTION_TIME({to}) */ COUNT(*) AS n FROM "
     "(SELECT d.dname, AVG(e.salary) FROM emp e JOIN dept d ON e.dno = d.dno WHERE d.budget > 800000 GROUP BY d.dname) q"),
    ("Q3 loc khoang luong", "SELECT /*+ MAX_EXECUTION_TIME({to}) */ COUNT(*) AS n FROM "
     "(SELECT dno, COUNT(*) FROM emp WHERE salary BETWEEN 50000 AND 50500 GROUP BY dno) q"),
]

# 3 cau hinh mau (dung khi khong co API hoac de doi chieu)
PRESETS = {
    "C1 tang buffer": ["SET GLOBAL innodb_buffer_pool_size = 536870912"],
    "C2 index dno": ["CREATE INDEX idx_dno ON emp(dno)"],
    "C3 index (dno,salary)": ["SET GLOBAL innodb_buffer_pool_size = 536870912",
                              "CREATE INDEX idx_dno_salary ON emp(dno, salary)",
                              "CREATE INDEX idx_salary ON emp(salary)"],
}

LT_PROMPT = """Recommend some configuration parameters for MySQL 8.0 to optimize the system's performance.
Parameters might include system-level configurations, like memory, query optimizer or physical design configurations, like index recommendations.
Each row in the following list has the following format:
{a join key A}:{all the joins with A in the workload}
emp.dno: dept.dno
Other predicates in the workload: emp.dno BETWEEN (range, GROUP BY dno); emp.salary BETWEEN (range); dept.budget > constant
Aggregates used: COUNT(*), AVG(emp.salary)
Table sizes: emp 1,000,000 rows; dept 100 rows.
The workload runs on a system with the following specs:
memory: {mem}
cores: {cores}
Return ONLY executable SQL statements, one per line, each ending with ';'.
Allowed statement types: SET GLOBAL <variable> = <value>; and CREATE INDEX <name> ON <table>(<columns>);"""

# Chi cho phep lenh an toan (giong y "DBA phe duyet")
SAFE_SQL = re.compile(r"^(SET\s+GLOBAL\s+(innodb_buffer_pool_size|sort_buffer_size|join_buffer_size|read_buffer_size|"
                      r"read_rnd_buffer_size|tmp_table_size|max_heap_table_size|innodb_io_capacity)\s*=\s*[\w.*]+"
                      r"|CREATE\s+INDEX\s+\w+\s+ON\s+(emp|dept)\s*\([\w\s,]+\))$", re.I)


def parse_config(text):
    """Lay cac lenh SQL an toan tu cau tra loi cua AI."""
    text = re.sub(r"```\w*", "", text or "")
    text = re.sub(r"_\(Model .*?\)_", "", text)   # bo dong ghi chu "model qua tai" truoc khi tach lenh
    ok, rejected = [], []
    for stmt in text.split(";"):
        s = " ".join(l.strip() for l in stmt.strip().splitlines() if l.strip() and not l.strip().startswith("--")).strip()
        if not s:
            continue
        (ok if SAFE_SQL.match(s) else rejected).append(s)
    return ok, rejected


MAX_POOL = 2 * 1024**3   # gioi han buffer pool 2GB de khong lam treo may demo


def normalize(stmt):
    """Doi '4G'/'512M' -> so byte (MySQL khong nhan hau to trong SET GLOBAL); gioi han buffer pool."""
    m = re.match(r"(?i)^SET\s+GLOBAL\s+(\w+)\s*=\s*([\w.*]+)$", stmt)
    if not m:
        return stmt
    var, val = m.group(1), m.group(2)
    mult = {"K": 1024, "M": 1024**2, "G": 1024**3}
    mm = re.match(r"(?i)^(\d+(?:\.\d+)?)([KMG])B?$", val)
    if mm:
        val = int(float(mm.group(1)) * mult[mm.group(2).upper()])
    elif re.match(r"^[\d*]+$", val):
        val = eval(val)            # chi gom chu so va dau *, an toan
    if var.lower() == "innodb_buffer_pool_size":
        val = min(int(val), MAX_POOL)
    return f"SET GLOBAL {var} = {val}"


def reset_config(conn):
    drop_secondary_indexes(conn)
    query(conn, "SET GLOBAL innodb_buffer_pool_size = 134217728")
    for v, d in (("sort_buffer_size", 262144), ("join_buffer_size", 262144), ("read_buffer_size", 131072),
                 ("read_rnd_buffer_size", 262144), ("tmp_table_size", 16777216), ("max_heap_table_size", 16777216),
                 ("innodb_io_capacity", 200)):
        query(conn, f"SET GLOBAL {v} = {d}")


def run_config(cfg, name, statements, timeout_ms=10000):
    """Ap dung 1 cau hinh, chay workload co timeout, tra ve dict ket qua."""
    conn = connect(cfg)
    try:
        reset_config(conn)
        t_apply, applied, errors = 0.0, [], []
        for s in statements:
            s = normalize(s)
            try:
                dt, _ = timed(conn, s)
                t_apply += dt
                applied.append(s)
            except pymysql.err.MySQLError as e:
                errors.append(f"{s} -> {e}")
        total, detail, timed_out = 0.0, [], False
        for qname, q in LT_WORKLOAD:
            try:
                dt, _ = timed(conn, q.format(to=timeout_ms))
            except pymysql.err.OperationalError as e:   # loi 3024 = vuot MAX_EXECUTION_TIME
                dt, timed_out = timeout_ms / 1000, True
            total += dt
            detail.append({"truy_van": qname, "giay": round(dt, 3)})
        return {"cau_hinh": name, "giay_workload": round(total, 3), "giay_ap_dung": round(t_apply, 2),
                "bi_timeout": timed_out, "lenh": applied, "loi": errors, "chi_tiet": detail}
    finally:
        conn.close()


def cleanup_after_tune(cfg):
    conn = connect(cfg)
    try:
        reset_config(conn)
    finally:
        conn.close()
