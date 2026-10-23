"""
agent_core.py - "D-Bot tu chu": nhieu AI (Claude + Gemini) cung hoi chan, tu chon cong cu.
Mo phong kien truc paper 5.2:
  - Kho kinh nghiem + tim kiem BM25 (paper Muc 4 & 6)
  - Agent tu chon cong cu that (chi doc), quan sat ket qua, suy luan tiep (paper Muc 6-7)
  - Quay lui khi cong cu khong cho thong tin (rut gon tu Tree of Thought, Muc 7)
  - Chief DBA dieu phoi + chuyen gia + phan bien (Muc 8: collaborative diagnosis)
  - Lenh sua chi chay khi DBA bam duyet
"""
import json
import math
import re
import time
import unicodedata

import pymysql

import demo_core as core
import tpch_core as tp

# =====================================================================
# 1. KHO KINH NGHIEM (8 the, 4 truong nhu paper) + BM25
# =====================================================================
KNOWLEDGE = [
    {"name": "missing_index",
     "content": "Truy van loc (WHERE) hoac noi (JOIN) theo cot khong co index phai quet toan bang, ton I/O va CPU.",
     "metrics": "rows_examined lon, no_index_used > 0, EXPLAIN type = ALL, key = NULL",
     "steps": "Xem truy van cham nhat; EXPLAIN; neu type = ALL va rows xap xi so dong bang thi la nguyen nhan; "
              "de xuat CREATE INDEX tren cot trong WHERE/JOIN."},
    {"name": "correlated_subquery",
     "content": "Truy van con tuong quan (dung cot cua truy van ngoai) bi chay lai cho tung dong cua truy van ngoai.",
     "metrics": "EXPLAIN select_type = DEPENDENT SUBQUERY, thoi gian lon du bang nho",
     "steps": "EXPLAIN truy van cham; neu co DEPENDENT SUBQUERY quet ALL thi la nguyen nhan; "
              "sua bang unnesting: tinh nhom 1 lan (GROUP BY) roi JOIN, hoac tao index tren cot noi cua truy van con."},
    {"name": "lock_contention",
     "content": "Giao tac giu khoa lau (chua COMMIT/ROLLBACK) lam giao tac khac phai cho, truy van bi treo.",
     "metrics": "sys.innodb_lock_waits co dong, innodb_trx co giao tac mo lau, CPU va disk thap",
     "steps": "Xem lock_waits va open_transactions; tim blocking_pid; xu ly giao tac dang chan (COMMIT/ROLLBACK, "
              "hoac KILL <pid> neu duoc phep); rut ngan giao tac."},
    {"name": "lack_statistic_info",
     "content": "Thong ke cua bang loi thoi lam bo toi uu uoc luong sai so dong, chon ke hoach te.",
     "metrics": "EXPLAIN rows lech xa so dong that, TABLE_ROWS khong khop",
     "steps": "So sanh EXPLAIN rows voi so dong that; neu lech nhieu thi chay ANALYZE TABLE."},
    {"name": "poor_join_performance",
     "content": "Phep noi giua bang lon khong co index tren khoa noi phai dung hash join / quet nhieu lan.",
     "metrics": "EXPLAIN Extra = Using join buffer (hash join), type = ALL tren bang lon",
     "steps": "Xem EXPLAIN; tim cot noi khong co index; de xuat CREATE INDEX tren khoa noi."},
    {"name": "redundant_index",
     "content": "Index trung lap hoac khong dung lam cham INSERT/UPDATE va ton dung luong.",
     "metrics": "nhieu index cung cot dau, index khong xuat hien trong EXPLAIN",
     "steps": "Xem table_info; neu co index trung tien to thi de xuat xoa (can DBA duyet)."},
    {"name": "fetch_large_data",
     "content": "Truy van tra ve hoac doc qua nhieu du lieu (SELECT *, khong LIMIT) lam cham va ton bo nho.",
     "metrics": "rows_sent lon, rows_examined lon",
     "steps": "Xem truy van cham; neu tra ve qua nhieu dong thi them dieu kien loc / LIMIT / chi chon cot can."},
    {"name": "buffer_pool_too_small",
     "content": "Buffer pool qua nho so voi du lieu hay doc lam MySQL phai doc dia lien tuc, cham va ton I/O.",
     "metrics": "ty_le_doc_trung_RAM < 99%, trang_trong = 0, buffer_pool_size nho",
     "steps": "Xem memory_status; neu ty le doc trung RAM thap va het trang trong thi de xuat tang innodb_buffer_pool_size (can DBA duyet)."},
    {"name": "sort_or_tmp_spill",
     "content": "Sap xep hoac bang tam vuot bo nho phai tran ra dia (sort merge, tmp table on disk).",
     "metrics": "Sort_merge_passes tang, Created_tmp_disk_tables tang",
     "steps": "Xem memory_status va EXPLAIN (Using filesort / Using temporary); tang sort_buffer_size/tmp_table_size hoac them index de khoi sap xep."},
    {"name": "cpu_contention",
     "content": "Qua nhieu phien chay cung luc tranh CPU.",
     "metrics": "processlist co nhieu truy van dang chay",
     "steps": "Xem processlist; neu nhieu phien nang cung luc thi gioi han dong thoi hoac toi uu truy van nang nhat."},
]


def _norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")
    return re.findall(r"[a-z0-9_]+", s)


_DOCS = [_norm(" ".join(k.values())) for k in KNOWLEDGE]
_AVG = sum(len(d) for d in _DOCS) / len(_DOCS)


def bm25(query, k1=1.5, b=0.75, top=2):
    q = _norm(query)
    N = len(_DOCS)
    scores = []
    for i, d in enumerate(_DOCS):
        sc = 0.0
        for t in set(q):
            df = sum(1 for dd in _DOCS if t in dd)
            if not df:
                continue
            idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
            tf = d.count(t)
            sc += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / _AVG))
        scores.append((sc, i))
    scores.sort(reverse=True)
    return [(round(s, 2), KNOWLEDGE[i]) for s, i in scores[:top] if s > 0]


# =====================================================================
# 2. KICH BAN SU CO (4 ca)
# =====================================================================
SCEN = {
    "kb1": {"label": "1 – Thiếu index (bảng emp 1 triệu dòng)", "db": core.DB},
    "kb2": {"label": "2 – Truy vấn con tương quan (emp_small)", "db": core.DB},
    "kb3": {"label": "3 – Tranh chấp khóa (UPDATE bị treo)", "db": core.DB},
    "tpch": {"label": "4 – TPC-H Q17 (báo cáo doanh thu chậm)", "db": tp.TDB},
}


def _conn(cfg, db):
    return core.connect(cfg, db=db)


def _ps_reset(cfg, db):
    c = _conn(cfg, db)
    try:
        core.query(c, "CALL sys.ps_truncate_all_tables(FALSE)")
    finally:
        c.close()


def create_incident(cfg, key):
    """Tao su co, tra ve dict state (trieu chung + so lieu goc de so sanh sau khi sua)."""
    st = {"key": key, "db": SCEN[key]["db"]}
    if key == "kb1":
        core.kb1_break(cfg)                       # xoa index phu + truncate performance_schema
        t = core.kb1_workload(cfg)
        st.update(before=t, symptom=f"Chức năng báo cáo theo phòng ban (ứng dụng gọi 30 truy vấn liên tiếp) mất {t:.1f} giây, "
                                     "CPU server tăng cao. Người dùng phàn nàn báo cáo rất chậm.")
    elif key == "kb2":
        _ps_reset(cfg, core.DB)
        t, n, _ = core.kb2_run(cfg, core.KB2_SLOW)
        st.update(before=t, before_result=n, orig_sql=core.KB2_SLOW,
                  symptom=f"Một báo cáo lương chạy mất {t:.1f} giây dù bảng dữ liệu chỉ có vài nghìn dòng. CPU cao.")
    elif key == "kb3":
        lk = core.LockScenario(cfg, wait_timeout=300)
        pid = lk.start_a()
        lk.start_b()
        _ps_reset(cfg, core.DB)
        st.update(lock=lk, holder_pid=pid, before="treo",
                  symptom="Một lệnh UPDATE rất đơn giản trên bảng emp (cập nhật lương 1 nhân viên) bị treo, "
                          "người dùng chờ mãi không xong. CPU và disk đều thấp.")
    elif key == "tpch":
        tp.q17_reset(cfg)
        _ps_reset(cfg, tp.TDB)
        t, v, _ = tp.q17_run(cfg, tp.Q17_SLOW)
        st.update(before=t, before_result=str(v), orig_sql=tp.Q17_SLOW,
                  symptom=f"Báo cáo doanh thu hằng năm (TPC-H Q17) chạy mất {t:.1f} giây, CPU cao. "
                          "Database tpch là dữ liệu bán buôn chuẩn TPC-H.")
    return st


# =====================================================================
# 3. CONG CU (chi doc) - agent tu chon
# =====================================================================
TOOLS = {
    "search_knowledge": "Tim the kinh nghiem DBA lien quan (BM25). args: {\"query\": \"mo ta van de\"}",
    "top_slow_queries": "5 cau SQL ton thoi gian nhat (performance_schema): so lan, giay TB, so dong doc TB, khong dung index. args: {}",
    "explain": "Ke hoach thuc thi cua 1 cau SELECT. args: {\"query_id\": so thu tu tu top_slow_queries} hoac {\"sql\": \"SELECT ...\"}",
    "table_info": "So dong, kich thuoc va cac index cua 1 bang. args: {\"table\": \"ten_bang\"}",
    "full_table_scans": "Cac cau SQL da phai quet toan bang (sys schema). args: {}",
    "lock_waits": "Ai dang cho khoa cua ai (sys.innodb_lock_waits). args: {}",
    "open_transactions": "Cac giao tac dang mo chua ket thuc (information_schema.innodb_trx). args: {}",
    "processlist": "Cac phien ket noi va lenh dang chay. args: {}",
    "workload_status": "Chi so CPU/tai: so phien dang chay, tong so cau lenh, so cau cham, ty le SELECT/UPDATE. args: {}",
    "memory_status": "Chi so bo nho: buffer pool (kich thuoc, ty le doc trung RAM, trang trong), bang tam tran ra dia, sort tran. args: {}",
}
AGENT_TOOLS = {   # 2 chuyen gia dung nhu paper 5.2 (Muc 8): CPU and Workload DBA, Memory and Database DBA
    "cpu": ["search_knowledge", "workload_status", "top_slow_queries", "explain", "full_table_scans", "processlist"],
    "mem": ["search_knowledge", "memory_status", "table_info", "lock_waits", "open_transactions"],
}


def _clip(s, n=1600):
    return s if len(s) <= n else s[:n] + " …(cat bot)"


def run_tool(cfg, st, name, args):
    """Thuc thi 1 cong cu. Tra ve (text_ket_qua, huu_ich: bool)."""
    db = st["db"]
    args = args or {}
    if name == "search_knowledge":
        hits = bm25(str(args.get("query", "")) or st["symptom"])
        if not hits:
            return "Khong tim thay the kinh nghiem phu hop.", False
        return "\n\n".join(f"[diem BM25 {s}] name: {k['name']}\ncontent: {k['content']}\nmetrics: {k['metrics']}\nsteps: {k['steps']}"
                           for s, k in hits), True
    c = _conn(cfg, db)
    try:
        if name == "top_slow_queries":
            rows = core.query(c, """
                SELECT DIGEST_TEXT AS q, COUNT_STAR AS so_lan, ROUND(AVG_TIMER_WAIT/1e12,3) AS giay_tb,
                       ROUND(SUM_TIMER_WAIT/1e12,3) AS tong_giay, ROUND(SUM_ROWS_EXAMINED/COUNT_STAR) AS dong_doc_tb,
                       SUM_NO_INDEX_USED AS khong_dung_index, QUERY_SAMPLE_TEXT AS sample
                FROM performance_schema.events_statements_summary_by_digest
                WHERE SCHEMA_NAME = %s AND DIGEST_TEXT REGEXP '^(SELECT|UPDATE|DELETE|INSERT)'
                  AND DIGEST_TEXT NOT LIKE '%%performance_schema%%' AND DIGEST_TEXT NOT LIKE '%%information_schema%%'
                  AND DIGEST_TEXT NOT LIKE '%%`sys`%%' AND DIGEST_TEXT NOT LIKE 'SELECT CONNECTION_ID%%'
                  AND DIGEST_TEXT NOT LIKE 'SELECT COUNT ( * ) AS `n` FROM%%'
                ORDER BY SUM_TIMER_WAIT DESC LIMIT 5""", (db,))
            st["slow"] = [r["sample"] for r in rows]
            if not rows:
                return "(rong) Chua co cau SQL nao duoc ghi nhan.", False
            out = []
            for i, r in enumerate(rows, 1):
                out.append(f"#{i}: so_lan={r['so_lan']}, giay_tb={r['giay_tb']}, tong_giay={r['tong_giay']}, "
                           f"dong_doc_tb={r['dong_doc_tb']}, khong_dung_index={r['khong_dung_index']}\n    SQL: {_clip(r['sample'] or r['q'], 400)}")
            return "\n".join(out), True
        if name == "explain":
            sql = args.get("sql")
            if args.get("query_id") is not None:
                try:
                    sql = st.get("slow", [])[int(args["query_id"]) - 1]
                except (IndexError, ValueError, TypeError):
                    return "Loi: query_id khong hop le – hay goi top_slow_queries truoc.", False
            sql = re.sub(r"/\*\+.*?\*/", "", sql or "").strip().rstrip(";")
            if not re.match(r"(?is)^select\b", sql):
                return "Loi: explain chi nhan cau SELECT.", False
            rows = core.query(c, "EXPLAIN " + sql)
            return core.fmt_rows(rows, ["id", "select_type", "table", "type", "possible_keys", "key", "rows", "filtered", "Extra"]), True
        if name == "table_info":
            t = str(args.get("table", ""))
            if not re.match(r"^\w+$", t):
                return "Loi: ten bang khong hop le.", False
            info = core.query(c, """SELECT TABLE_ROWS AS so_dong_uoc_tinh, ROUND((DATA_LENGTH+INDEX_LENGTH)/1048576,1) AS mb
                                    FROM information_schema.tables WHERE table_schema=%s AND table_name=%s""", (db, t))
            if not info:
                return f"Loi: khong co bang {t}.", False
            idx = core.query(c, """SELECT INDEX_NAME AS index_name, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS cot
                                   FROM information_schema.statistics WHERE table_schema=%s AND table_name=%s
                                   GROUP BY INDEX_NAME""", (db, t))
            return f"Bang {t}: {core.fmt_rows(info)}\nIndex:\n{core.fmt_rows(idx)}", True
        if name == "full_table_scans":
            rows = core.query(c, """SELECT LEFT(query,200) AS query, exec_count, total_latency, rows_examined_avg
                                    FROM sys.statements_with_full_table_scans WHERE db=%s
                                      AND query NOT LIKE '%%information_schema%%' AND query NOT LIKE '%%performance_schema%%' AND query NOT LIKE 'EXPLAIN%%'
                                    ORDER BY exec_count DESC LIMIT 5""", (db,))
            return (core.fmt_rows(rows), True) if rows else ("(rong) Khong co cau nao quet toan bang.", False)
        if name == "lock_waits":
            rows = core.query(c, """SELECT wait_age, locked_table, locked_type, waiting_pid, waiting_query,
                                           blocking_pid, blocking_query, sql_kill_blocking_connection FROM sys.innodb_lock_waits""")
            return (core.fmt_rows(rows), True) if rows else ("(rong) Khong co ai dang cho khoa.", False)
        if name == "open_transactions":
            rows = core.query(c, """SELECT trx_mysql_thread_id AS pid, trx_state, TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS giay_da_mo,
                                           trx_rows_locked, LEFT(trx_query,150) AS dang_chay FROM information_schema.innodb_trx""")
            return (core.fmt_rows(rows), True) if rows else ("(rong) Khong co giao tac nao dang mo.", False)
        if name == "workload_status":
            g = {r["Variable_name"]: r["Value"] for r in core.query(c, """SHOW GLOBAL STATUS WHERE Variable_name IN
                 ('Threads_running','Threads_connected','Questions','Slow_queries','Com_select','Com_update',
                  'Com_insert','Com_delete','Uptime')""")}
            act = core.query(c, """SELECT COUNT(*) AS n FROM information_schema.PROCESSLIST
                                   WHERE COMMAND = 'Query' AND ID <> CONNECTION_ID()""")[0]["n"]
            return (f"Threads_running={g.get('Threads_running')}, Threads_connected={g.get('Threads_connected')}, "
                    f"cau_dang_chay_khac={act}, Questions={g.get('Questions')}, Slow_queries={g.get('Slow_queries')}, "
                    f"Com_select={g.get('Com_select')}, Com_update={g.get('Com_update')}, Com_insert={g.get('Com_insert')}, "
                    f"Uptime_s={g.get('Uptime')}"), True
        if name == "memory_status":
            g = {r["VARIABLE_NAME"]: int(r["VARIABLE_VALUE"]) for r in core.query(c, """SELECT VARIABLE_NAME, VARIABLE_VALUE
                 FROM performance_schema.global_status WHERE VARIABLE_NAME IN ('Innodb_buffer_pool_read_requests',
                 'Innodb_buffer_pool_reads','Innodb_buffer_pool_pages_free','Innodb_buffer_pool_pages_total',
                 'Created_tmp_tables','Created_tmp_disk_tables','Sort_merge_passes')""")}
            v = core.query(c, "SELECT @@innodb_buffer_pool_size AS bp, @@sort_buffer_size AS sb, @@tmp_table_size AS tt")[0]
            req, rd = g.get("Innodb_buffer_pool_read_requests", 0), g.get("Innodb_buffer_pool_reads", 0)
            hit = 100.0 * (1 - rd / req) if req else 100.0
            return (f"buffer_pool_size={int(v['bp'])//1048576}MB, ty_le_doc_trung_RAM={hit:.2f}%, "
                    f"trang_trong={g.get('Innodb_buffer_pool_pages_free')}/{g.get('Innodb_buffer_pool_pages_total')}, "
                    f"bang_tam={g.get('Created_tmp_tables')} (tran ra dia {g.get('Created_tmp_disk_tables')}), "
                    f"Sort_merge_passes={g.get('Sort_merge_passes')}, sort_buffer={int(v['sb'])//1024}KB, "
                    f"tmp_table_size={int(v['tt'])//1048576}MB"), True
        if name == "processlist":
            rows = core.query(c, """SELECT ID AS pid, USER, DB, COMMAND, TIME AS giay, STATE, LEFT(INFO,150) AS lenh
                                    FROM information_schema.PROCESSLIST WHERE ID <> CONNECTION_ID() AND USER <> 'event_scheduler'""")
            return (core.fmt_rows(rows), True) if rows else ("(rong)", False)
        return f"Loi: khong co cong cu '{name}'.", False
    except pymysql.err.MySQLError as e:
        return f"Loi MySQL: {e}", False
    finally:
        c.close()


# =====================================================================
# 3b. NHA CUNG CAP AI (nhieu hang, co lua chon mien phi)
# =====================================================================
import os
import shutil
import subprocess
import requests

# OpenAI-compatible: base_url, model mac dinh, bien moi truong chua key
OPENAI_COMPAT = {
    "Groq (miễn phí)": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "OpenRouter (model :free)": ("https://openrouter.ai/api/v1", "meta-llama/llama-3.3-70b-instruct:free", "OPENROUTER_API_KEY"),
    "GitHub Models (miễn phí)": ("https://models.github.ai/inference", "openai/gpt-4o", "GITHUB_TOKEN"),
    "Cerebras (miễn phí)": ("https://api.cerebras.ai/v1", "llama-3.3-70b", "CEREBRAS_API_KEY"),
    "Mistral (miễn phí)": ("https://api.mistral.ai/v1", "mistral-small-latest", "MISTRAL_API_KEY"),
    "Ollama (chạy trên máy)": ("http://localhost:11434/v1", "qwen2.5:7b", ""),
}
PROVIDERS = {
    "Gemini": ("gemini-3.8-flash", "GEMINI_API_KEY"),
    "Claude API": ("claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
    "Claude Code (gói Pro)": ("sonnet", ""),
    **{k: (v[1], v[2]) for k, v in OPENAI_COMPAT.items()},
}
NEEDS_KEY = {p for p, (_, env) in PROVIDERS.items() if env and p != "Ollama (chạy trên máy)"}


def _openai_compat(base, key, model, prompt, temperature, timeout=120):
    for wait in (0, 4, 10):
        if wait:
            time.sleep(wait)
        r = requests.post(base.rstrip("/") + "/chat/completions", timeout=timeout,
                          headers={"Authorization": f"Bearer {key or 'none'}", "Content-Type": "application/json"},
                          json={"model": model, "temperature": temperature, "max_tokens": 1500,
                                "messages": [{"role": "user", "content": prompt}]})
        if r.status_code in (429, 500, 502, 503, 504):
            continue
        if r.status_code != 200:
            raise RuntimeError(f"API loi {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"] or ""
    raise RuntimeError(f"API qua tai (loi {r.status_code}) – thu lai sau hoac doi nha cung cap.")


def _claude_code(model, prompt, timeout=240):
    """Goi Claude Code (dung goi Pro/Max da dang nhap, khong can API key)."""
    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        raise RuntimeError("Chua cai Claude Code. Cai: npm install -g @anthropic-ai/claude-code, roi go 'claude' de dang nhap.")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}   # tranh tinh tien API
    cmd = [exe, "-p", "Thuc hien dung yeu cau trong noi dung duoc gui qua stdin. Chi tra loi, khong dung cong cu.",
           "--output-format", "text", "--tools", "", "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"Claude Code loi: {(r.stderr or r.stdout)[:300]}")
    return r.stdout


def llm_call(llm, prompt, temperature=0.3):
    """llm = dict(provider, key, model)."""
    p = llm["provider"]
    if p == "Gemini":
        return core.ask_ai("Gemini", llm["key"], llm["model"], prompt, temperature)
    if p in ("Claude API", "Claude"):
        return core.ask_ai("Claude", llm["key"], llm["model"], prompt, temperature)
    if p == "Claude Code (gói Pro)":
        return _claude_code(llm.get("model"), prompt)
    if p in OPENAI_COMPAT:
        return _openai_compat(OPENAI_COMPAT[p][0], llm.get("key"), llm["model"], prompt, temperature)
    raise ValueError(f"Nha cung cap khong ho tro: {p}")


# =====================================================================
# 4. GOI AI + DOC JSON
# =====================================================================
def parse_json(text):
    t = re.sub(r"```(json)?", "", text or "")
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("khong co JSON")
    return json.loads(t[i:j + 1])


def ask_json(llm, prompt):
    """llm = dict(provider, key, model). Hoi AI, doc JSON; sai dinh dang thi hoi lai 1 lan."""
    raw = llm_call(llm, prompt, 0.3)
    try:
        return parse_json(raw), raw
    except (ValueError, json.JSONDecodeError):
        raw2 = llm_call(llm, prompt + "\n\nCHU Y: Lan truoc ban tra loi sai dinh dang. Chi tra ve DUY NHAT 1 object JSON hop le.", 0.2)
        return parse_json(raw2), raw2


RULES = ("Tra loi bang tieng Viet. Chi tra ve DUY NHAT 1 object JSON (khong markdown, khong giai thich ngoai JSON).")

ROLE_DESC = {
    "cpu": "CPU and Workload DBA – chuyen gia CPU va tai cong viec: cau SQL ton CPU, quet toan bang, ke hoach thuc thi, phien dang chay",
    "mem": "Memory and Database DBA – chuyen gia bo nho va ben trong database: buffer pool, bang tam/sort tran dia, bang va index, khoa va giao tac",
}


def chief_plan_prompt(st):
    return f"""Ban la Chief DBA, dieu phoi mot nhom AI chan doan su co MySQL 8 (database `{st['db']}`).
Trieu chung nhan duoc (chi co vay, chua co bang chung):
{st['symptom']}

Nhom chuyen gia:
- cpu: {ROLE_DESC['cpu']}
- mem: {ROLE_DESC['mem']}

Nhiem vu: phan tich trieu chung, chon chuyen gia can giao viec (1 hoac 2, theo thu tu uu tien).
{RULES}
Dang JSON: {{"thought": "phan tich ngan", "assign": ["cpu" hoac "mem", ...]}}"""


def expert_prompt(role, st, board, history, max_steps):
    tools = "\n".join(f"- {t}: {TOOLS[t]}" for t in AGENT_TOOLS[role])
    lines = []
    for i, h in enumerate(history):
        s = f"Buoc {i+1}: goi {h['action']} {json.dumps(h['args'], ensure_ascii=False)}\nKet qua: {h['obs']}"
        if h.get("note"):
            s += f"\n(!) {h['note']}"
        lines.append(s)
    hist = "\n".join(lines) or "(chua co)"
    left = max_steps - len(history)
    return f"""Ban la {ROLE_DESC[role]}, thanh vien nhom chan doan MySQL 8 (database `{st['db']}`).
Trieu chung: {st['symptom']}

Ghi chu chung cua nhom (cac chuyen gia khac da tim thay):
{board or '(chua co)'}

Cong cu ban duoc dung (chi doc, khong lam thay doi he thong):
{tools}

Quy tac:
1. Moi luot chi goi 1 cong cu. Nen bat dau bang search_knowledge de lay kinh nghiem phu hop.
2. Chi ket luan khi co BANG CHUNG tu ket qua cong cu. Neu cong cu khong cho thong tin huu ich, hay thu huong khac.
3. Neu van de khong thuoc chuyen mon cua ban, ket luan "khong thuoc linh vuc" kem bang chung.
4. Con {left} luot. {"Day la luot cuoi: BAT BUOC tra ve final." if left <= 1 else ""}

Lich su cac buoc cua ban:
{hist}

{RULES}
Neu can goi cong cu: {{"thought": "vi sao chon cong cu nay", "action": "ten_cong_cu", "args": {{...}}}}
Neu da du bang chung: {{"thought": "...", "final": {{"cause": "nguyen nhan goc", "evidence": "bang chung cu the tu cong cu", "fix_sql": "1 lenh SQL sua (CREATE INDEX / ANALYZE TABLE / KILL <pid> / cau SELECT viet lai) hoac de trong"}}}}"""


def chief_final_prompt(st, reports, review=None, draft=None):
    rep = "\n\n".join(f"[{r['role']} – {r['model']}]\nnguyen nhan: {r['cause']}\nbang chung: {r['evidence']}\nfix_sql: {r['fix_sql']}"
                      for r in reports)
    extra = ""
    if review:
        extra = (f"\n\nKet luan nhap cua ban truoc do: {json.dumps(draft, ensure_ascii=False)}\n"
                 f"Bac si phan bien KHONG dong y: {review.get('comment')}\nGoi y cua phan bien: {review.get('fix_sql','')}\n"
                 "Hay xem lai va dua ra ket luan cuoi cung.")
    return f"""Ban la Chief DBA. Trieu chung: {st['symptom']}
Bao cao cua cac chuyen gia:
{rep}{extra}

Tong hop thanh ket luan cuoi cung. fix_sql chi gom 1 lenh: CREATE INDEX, ANALYZE TABLE, KILL <pid>, hoac 1 cau SELECT viet lai
(cau viet lai phai cho CUNG ket qua voi cau goc). Lenh se chi chay khi DBA (con nguoi) duyet.
{RULES}
Dang JSON: {{"thought": "...", "cause": "...", "evidence": "...", "fix_sql": "...", "risk": "rui ro khi chay lenh"}}"""


def reviewer_prompt(st, reports, draft):
    rep = "\n\n".join(f"[{r['role']}] {r['cause']} | bang chung: {r['evidence']}" for r in reports)
    return f"""Ban la bac si phan bien (mot AI khac hang voi Chief DBA). Hay kiem tra ket luan cua Chief DBA.
Trieu chung: {st['symptom']}
Bang chung cac chuyen gia thu duoc:
{rep}
Ket luan cua Chief DBA: {json.dumps(draft, ensure_ascii=False)}

Kiem tra: nguyen nhan co khop bang chung khong? Lenh sua co dung, an toan, giu nguyen ket qua khong?
{RULES}
Dang JSON: {{"agree": true hoac false, "comment": "nhan xet ngan", "fix_sql": "lenh de xuat neu khong dong y, nguoc lai de trong"}}"""


# =====================================================================
# 5. DIEU PHOI HOI CHAN
# =====================================================================
def diagnose(cfg, st, llms, on_event=lambda ev: None, max_steps=5):
    """llms = {"chief":{...}, "cpu":{...}, "mem":{...}, ["reviewer":{...}]}. Tra ve (trace, final).
    Khong co "reviewer" -> dung dung 3 agent nhu paper (Chief DBA tu tong hop)."""
    trace = []

    def emit(**ev):
        ev["t"] = time.strftime("%H:%M:%S")
        trace.append(ev)
        on_event(ev)

    name = lambda r: f"{llms[r]['provider']} · {llms[r]['model']}"
    emit(agent="Hệ thống", kind="input", text=st["symptom"])

    # (1) Chief DBA lap ke hoach
    plan, _ = ask_json(llms["chief"], chief_plan_prompt(st))
    assign = [a for a in plan.get("assign", []) if a in AGENT_TOOLS][:2] or ["cpu", "mem"]
    emit(agent="Chief DBA", model=name("chief"), kind="plan", text=plan.get("thought", ""), assign=assign)

    # (2) Chuyen gia lan luot lam viec (chat order), thay ket qua cua nhau (visibility)
    board, reports = "", []
    for role in assign:
        history = []
        final = None
        for step in range(max_steps):
            dec, _ = ask_json(llms[role], expert_prompt(role, st, board, history, max_steps))
            if "final" in dec or "action" not in dec:
                final = dec.get("final", dec)
                emit(agent=role, model=name(role), kind="thought", text=dec.get("thought", ""))
                break
            act, args = dec.get("action"), dec.get("args") or {}
            emit(agent=role, model=name(role), kind="thought", text=dec.get("thought", ""))
            if act not in AGENT_TOOLS[role]:
                obs, ok = f"Loi: cong cu '{act}' khong duoc phep cho {role}.", False
            elif any(h["action"] == act and h["args"] == args for h in history):
                obs, ok = "Cong cu nay voi cung tham so da goi roi – khong co thong tin moi.", False
            else:
                obs, ok = run_tool(cfg, st, act, args)
            obs = _clip(obs)
            emit(agent=role, model=name(role), kind="tool", tool=act, args=args, text=obs, ok=ok)
            h = {"action": act, "args": args, "obs": obs}
            if not ok:
                h["note"] = "Buoc nay khong cho thong tin huu ich – hay QUAY LAI va thu huong khac."
                emit(agent=role, model=name(role), kind="backtrack", text=h["note"])
            history.append(h)
        if final is None:   # het luot ma chua ket luan -> ep ket luan
            dec, _ = ask_json(llms[role], expert_prompt(role, st, board, history, len(history) + 1))
            final = dec.get("final", dec)
        rep = {"role": role, "model": name(role), "cause": final.get("cause", ""),
               "evidence": final.get("evidence", ""), "fix_sql": final.get("fix_sql", "")}
        reports.append(rep)
        emit(agent=role, model=name(role), kind="report", text=rep["cause"], evidence=rep["evidence"], fix_sql=rep["fix_sql"])
        board += f"- {role}: {rep['cause']} (bang chung: {rep['evidence']})\n"

    # (3) Chief tong hop -> (4) phan bien (AI khac hang) -> neu khong dong y, Chief xem lai 1 lan
    draft, _ = ask_json(llms["chief"], chief_final_prompt(st, reports))
    emit(agent="Chief DBA", model=name("chief"), kind="draft", text=draft.get("cause", ""), evidence=draft.get("evidence", ""),
         fix_sql=draft.get("fix_sql", ""))
    final = draft
    if llms.get("reviewer"):
        review, _ = ask_json(llms["reviewer"], reviewer_prompt(st, reports, draft))
        agree = bool(review.get("agree"))
        emit(agent="Phản biện", model=name("reviewer"), kind="review", text=review.get("comment", ""), agree=agree,
             fix_sql=review.get("fix_sql", ""))
    else:
        agree = True
    if not agree:
        final, _ = ask_json(llms["chief"], chief_final_prompt(st, reports, review, draft))
        emit(agent="Chief DBA", model=name("chief"), kind="revise", text=final.get("cause", ""),
             evidence=final.get("evidence", ""), fix_sql=final.get("fix_sql", ""))
    emit(agent="Chief DBA", model=name("chief"), kind="final", text=final.get("cause", ""), evidence=final.get("evidence", ""),
         fix_sql=final.get("fix_sql", ""), risk=final.get("risk", ""))
    return trace, final


# =====================================================================
# 6. DBA DUYET -> CHAY LENH SUA -> DO LAI
# =====================================================================
SAFE_FIX = re.compile(r"(?is)^\s*(create\s+index\s+\w+\s+on\s+\w+\s*\([\w\s,]+\)|analyze\s+table\s+[\w\s,]+|kill\s+\d+|select\b.+)\s*;?\s*$")


def apply_fix(cfg, st, fix_sql):
    """Chay lenh da duoc DBA duyet va do lai. Tra ve dict ket qua."""
    sql = (fix_sql or "").strip().rstrip(";").strip()
    sql = re.sub(r"(?s)^```\w*|```$", "", sql).strip()
    if ";" in sql:
        sql = sql.split(";")[0].strip()
    if not SAFE_FIX.match(sql):
        return {"ok": False, "msg": f"Lệnh không nằm trong danh sách cho phép, app không chạy: {sql[:200]}"}
    key, db = st["key"], st["db"]
    c = _conn(cfg, db)
    try:
        if re.match(r"(?i)select", sql):
            t, rows = core.timed(c, sql)
            val = list(rows[0].values())[0] if rows else None
            same = str(val) == str(st.get("before_result"))
            return {"ok": True, "kind": "rewrite", "before": st.get("before"), "after": t, "after_result": val,
                    "before_result": st.get("before_result"), "same": same,
                    "msg": "Cùng kết quả với câu gốc." if same else "⚠️ Kết quả KHÁC câu gốc – lời khuyên này sai!"}
        if re.match(r"(?i)kill", sql):
            core.query(c, sql)
            time.sleep(1.5)
            lk = st.get("lock")
            res = lk.b_result if lk else {}
            if lk and lk.b_thread:
                lk.b_thread.join(timeout=10)
                res = lk.b_result
            left = core.query(c, "SELECT COUNT(*) AS n FROM sys.innodb_lock_waits")[0]["n"]
            return {"ok": True, "kind": "kill", "msg": f"Đã KILL. Phiên bị treo: {res.get('status')}; số dòng chờ khóa còn lại: {left}"}
        core.query(c, sql)                               # CREATE INDEX / ANALYZE TABLE
    except pymysql.err.MySQLError as e:
        return {"ok": False, "msg": f"Lỗi MySQL khi chạy lệnh: {e}"}
    finally:
        c.close()
    # do lai
    if key == "kb1":
        after = core.kb1_workload(cfg)
    elif key == "kb2":
        after, _, _ = core.kb2_run(cfg, core.KB2_SLOW)
    elif key == "tpch":
        after, _, _ = tp.q17_run(cfg, tp.Q17_SLOW)
    else:
        after = None
    return {"ok": True, "kind": "ddl", "before": st.get("before"), "after": after, "msg": "Đã chạy lệnh và đo lại."}


def cleanup(cfg, st):
    lk = st.get("lock") if st else None
    if lk:
        try:
            lk.fix()
        except Exception:
            pass


# =====================================================================
# 7. HOI THEM QUA CHAT (buoc 5-6) va TU KIEM TRA SAU XU LY (buoc 9)
# =====================================================================
ALL_TOOLS = list(TOOLS)


def _react(cfg, st, llm, make_prompt, tools, max_steps, emit, who):
    """Vong lap: AI tu chon cong cu -> quan sat -> ... -> tra ve dict 'final'."""
    history = []
    for step in range(max_steps):
        dec, _ = ask_json(llm, make_prompt(history, max_steps - len(history)))
        if "final" in dec or "action" not in dec:
            if dec.get("thought"):
                emit(agent=who, kind="thought", text=dec.get("thought", ""))
            return dec.get("final", dec)
        act, args = dec.get("action"), dec.get("args") or {}
        emit(agent=who, kind="thought", text=dec.get("thought", ""))
        if act not in tools:
            obs, ok = f"Loi: khong co cong cu '{act}'.", False
        elif any(h["action"] == act and h["args"] == args for h in history):
            obs, ok = "Da goi cong cu nay voi cung tham so – khong co thong tin moi.", False
        else:
            obs, ok = run_tool(cfg, st, act, args)
        obs = _clip(obs)
        emit(agent=who, kind="tool", tool=act, args=args, text=obs, ok=ok)
        h = {"action": act, "args": args, "obs": obs}
        if not ok:
            h["note"] = "Buoc nay khong cho thong tin huu ich – hay thu huong khac."
            emit(agent=who, kind="backtrack", text=h["note"])
        history.append(h)
    dec, _ = ask_json(llm, make_prompt(history, 0) + "\nDA HET LUOT: bat buoc tra ve final ngay.")
    return dec.get("final", dec)


def _hist_text(history):
    out = []
    for i, h in enumerate(history):
        s = f"Buoc {i+1}: goi {h['action']} {json.dumps(h['args'], ensure_ascii=False)}\nKet qua: {h['obs']}"
        if h.get("note"):
            s += f"\n(!) {h['note']}"
        out.append(s)
    return "\n".join(out) or "(chua co)"


def _tool_list(tools):
    return "\n".join(f"- {t}: {TOOLS[t]}" for t in tools)


def incident_brief(inc):
    f = inc.get("final") or {}
    return (f"Incident {inc['id']} ({inc['title']}), database `{inc['db']}`.\nTrieu chung: {inc['symptom']}\n"
            f"Ket luan hien tai: {f.get('cause','(chua co)')}\nBang chung: {f.get('evidence','')}\n"
            f"Lenh de xuat: {f.get('fix_sql','')}")


def chat_answer(cfg, inc, llm, question, emit, max_steps=4):
    """User hoi them -> Chief DBA tu goi them cong cu neu can -> tra loi (co the doi de xuat)."""
    st = inc["ctx"]
    convo = "\n".join(f"{m['role']}: {m['text']}" for m in inc.get("chat", [])[-6:])

    def mk(history, left):
        return f"""Ban la Chief DBA dang xu ly su co MySQL 8.
{incident_brief(inc)}

Hoi thoai truoc do:
{convo or '(chua co)'}

DBA hoi: "{question}"

Ban co the goi them cong cu (chi doc) de tra loi cho chinh xac:
{_tool_list(ALL_TOOLS)}
Lich su cong cu trong luot hoi nay:
{_hist_text(history)}
Con {left} luot goi cong cu.

{RULES}
Neu can goi cong cu: {{"thought": "...", "action": "ten_cong_cu", "args": {{...}}}}
Neu da tra loi duoc: {{"thought": "...", "final": {{"answer": "cau tra loi cho DBA, dan bang chung", "new_fix_sql": "lenh de xuat moi neu muon thay doi, nguoc lai de trong"}}}}"""
    return _react(cfg, st, llm, mk, ALL_TOOLS, max_steps, emit, "Chief DBA")


def verify_after(cfg, inc, llm, action_result, emit, max_steps=3):
    """Sau khi chay lenh: AI tu goi cong cu kiem tra su co con hay het."""
    st = inc["ctx"]

    def mk(history, left):
        return f"""Ban la Chief DBA. DBA da duyet va he thong da chay lenh sua.
{incident_brief(inc)}
Ket qua app ghi nhan sau khi chay lenh: {json.dumps(action_result, ensure_ascii=False, default=str)}

Hay TU KIEM TRA lai bang cong cu (chi doc) xem su co da het chua (vd: EXPLAIN lai, xem lock_waits...).
{_tool_list(ALL_TOOLS)}
Lich su kiem tra:
{_hist_text(history)}
Con {left} luot.

{RULES}
Neu can goi cong cu: {{"thought": "...", "action": "ten_cong_cu", "args": {{...}}}}
Khi da du bang chung: {{"thought": "...", "final": {{"resolved": true hoac false, "summary": "bao cao dong su co: da lam gi, bang chung truoc/sau"}}}}"""
    return _react(cfg, st, llm, mk, ALL_TOOLS, max_steps, emit, "Chief DBA")


# =====================================================================
# 8. CHAY LENH DA DUYET cho incident do giam sat tao ra (dua tren cau SQL mau)
# =====================================================================
def run_sample(cfg, db, sql, timeout_s=120):
    sql = re.sub(r"/\*\+.*?\*/", "", sql or "").strip().rstrip(";")
    c = _conn(cfg, db)
    try:
        core.query(c, f"SET SESSION max_execution_time = {timeout_s*1000}")   # gioi han thoi gian, khong doi dau van tay SQL
        try:
            t, rows = core.timed(c, sql)
            return t, (list(rows[0].values())[0] if rows else None)
        except pymysql.err.OperationalError:
            return float(timeout_s), "timeout"
    finally:
        c.close()


def apply_action(cfg, inc, fix_sql):
    sql = re.sub(r"(?s)^```\w*|```$", "", (fix_sql or "").strip()).strip().rstrip(";").strip()
    if ";" in sql:
        sql = sql.split(";")[0].strip()
    if not SAFE_FIX.match(sql):
        return {"ok": False, "msg": f"Lệnh không nằm trong danh sách cho phép, app không chạy: {sql[:200]}"}
    db, sample = inc["db"], inc.get("sample_sql")
    try:
        if re.match(r"(?i)select", sql):
            t0, v0 = run_sample(cfg, db, sample) if sample else (None, None)
            t1, v1 = run_sample(cfg, db, sql)
            same = str(v0) == str(v1)
            return {"ok": True, "kind": "rewrite", "before": t0, "after": t1, "before_result": v0, "after_result": v1,
                    "same": same, "msg": "Cùng kết quả với câu gốc." if same else "⚠️ Kết quả KHÁC câu gốc – không nên áp dụng!"}
        c = _conn(cfg, db)
        try:
            core.query(c, sql)
            if re.match(r"(?i)kill", sql):
                time.sleep(2)
                left = core.query(c, "SELECT COUNT(*) AS n FROM sys.innodb_lock_waits")[0]["n"]
                return {"ok": True, "kind": "kill", "lock_waits_after": left,
                        "msg": f"Đã KILL. Số phiên còn chờ khóa: {left}"}
        finally:
            c.close()
        before = inc.get("metrics", {}).get("avg_s")
        after = run_sample(cfg, db, sample)[0] if sample and re.match(r"(?is)^\s*(/\*.*?\*/\s*)?select", sample) else None
        return {"ok": True, "kind": "ddl", "before": before, "after": after, "msg": "Đã chạy lệnh và đo lại câu SQL gốc."}
    except pymysql.err.MySQLError as e:
        return {"ok": False, "msg": f"Lỗi MySQL: {e}"}
