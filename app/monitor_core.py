"""
monitor_core.py - Vong doi su co (incident) cho D-Bot tu chu:
  1. Giam sat realtime (performance_schema + sys.innodb_lock_waits)  ->  2. tu tao incident
  3. tu chan doan (nhieu AI)  ->  4. tom tat  ->  5-6. hoi them qua chat  ->  7. de xuat
  8. DBA duyet / tu choi  ->  9. AI tu kiem tra lai va dong incident
Chay nen bang thread; giao dien Streamlit chi doc trang thai tu Hub.
"""
import threading
import time
import traceback

import demo_core as core
import tpch_core as tp
import agent_core as ag

WATCH_DBS = (core.DB, tp.TDB)
STATUS = {"new": "🆕 Mới", "diag": "🩺 Đang chẩn đoán", "wait": "⏳ Chờ DBA duyệt", "chat": "💬 Đang trả lời",
          "fixing": "🛠️ Đang xử lý", "verify": "🔍 Đang kiểm tra lại", "closed": "✅ Đã đóng",
          "open": "⚠️ Chưa khỏi", "rejected": "🚫 DBA từ chối", "error": "❌ Lỗi"}


class Hub:
    def __init__(self):
        self.lock = threading.RLock()
        self.incidents = []
        self.cfg = None
        self.llms = None
        self.auto = True
        self.interval = 3.0
        self.slow_avg = 1.0        # giay / lan chay
        self.slow_total = 1.5      # tong giay trong 1 chu ky
        self.lock_age = 5          # giay cho khoa
        self.running = False
        self.thread = None
        self.last_poll = None
        self.last_metrics = {}
        self.log = []
        self._snap = {}
        self._seen = set()
        self.fault_lock = None     # LockScenario dang mo (ca tranh chap khoa)
        self.fault_status = ""
        self.mute = False

    # ------------------------------------------------------------ giam sat
    def start(self, cfg, llms, auto=True, interval=3.0):
        with self.lock:
            self.cfg, self.llms, self.auto, self.interval = cfg, llms, auto, interval
            if self.running:
                return
            self.running = True
            self._snap = self._snapshot()
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            self._log("Bật giám sát realtime.")

    def stop(self):
        self.running = False
        self._log("Tắt giám sát.")

    def _log(self, msg):
        with self.lock:
            self.log.append(f"{time.strftime('%H:%M:%S')} {msg}")
            self.log = self.log[-30:]

    def _snapshot(self):
        snap = {}
        c = core.connect(self.cfg, db=None)
        try:
            rows = core.query(c, """
                SELECT SCHEMA_NAME AS db, DIGEST AS d, DIGEST_TEXT AS q, COUNT_STAR AS n, SUM_TIMER_WAIT/1e12 AS t,
                       SUM_ROWS_EXAMINED AS r, SUM_NO_INDEX_USED AS ni, QUERY_SAMPLE_TEXT AS sample
                FROM performance_schema.events_statements_summary_by_digest
                WHERE SCHEMA_NAME IN (%s, %s) AND DIGEST_TEXT REGEXP '^(SELECT|UPDATE|DELETE)'
                  AND DIGEST_TEXT NOT LIKE '%%performance_schema%%' AND DIGEST_TEXT NOT LIKE '%%information_schema%%'
                  AND DIGEST_TEXT NOT LIKE '%%`sys`%%' AND DIGEST_TEXT NOT LIKE 'SELECT CONNECTION_ID%%'
                  AND DIGEST_TEXT NOT LIKE 'SELECT COUNT ( * ) AS `n` FROM%%'""", WATCH_DBS)
            for r in rows:
                snap[(r["db"], r["d"])] = r
        finally:
            c.close()
        return snap

    def _loop(self):
        while self.running:
            try:
                self.poll()
            except Exception as e:
                self._log(f"Lỗi giám sát: {e}")
            time.sleep(self.interval)

    def poll(self):
        now = self._snapshot()
        alerts = []
        top = None
        for k, r in now.items():
            old = self._snap.get(k)
            if old and r["n"] < old["n"]:      # thong ke vua bi xoa -> tinh lai tu 0
                old = None
            dn = r["n"] - (old["n"] if old else 0)
            dt = float(r["t"]) - (float(old["t"]) if old else 0.0)
            if dn <= 0:
                continue
            avg = dt / dn
            if top is None or dt > top[1]:
                top = (r["q"][:60], round(dt, 2))
            if (avg >= self.slow_avg or dt >= self.slow_total) and k not in self._seen:
                self._seen.add(k)
                dr = (r["r"] - (old["r"] if old else 0)) / dn
                alerts.append(("slow", r["db"], r, {"so_lan": int(dn), "tong_s": round(dt, 2), "avg_s": round(avg, 3),
                                                     "dong_doc_tb": int(dr)}))
        self._snap = now
        if self.mute:                          # dang chay lenh sua / kiem tra lai -> khong bao dong
            return
        c = core.connect(self.cfg, db=None)
        try:
            lw = core.query(c, """SELECT wait_age_secs, locked_table, waiting_query, blocking_pid
                                  FROM sys.innodb_lock_waits""")
        finally:
            c.close()
        for w in lw:
            key = ("lock", w["blocking_pid"])
            if int(w["wait_age_secs"] or 0) >= self.lock_age and key not in self._seen:
                self._seen.add(key)
                db = (w["locked_table"] or "").split(".")[0].strip("`") or core.DB
                alerts.append(("lock", db, w, {"cho_giay": int(w["wait_age_secs"]), "bang": w["locked_table"]}))
        with self.lock:
            self.last_poll = time.strftime("%H:%M:%S")
            self.last_metrics = {"so_cau_moi_chay": sum(1 for k, r in now.items()), "phien_cho_khoa": len(lw),
                                 "nang_nhat_chu_ky": top}
        for a in alerts:
            self.new_incident(*a)

    # ------------------------------------------------------------ incident
    def new_incident(self, kind, db, row, metrics):
        with self.lock:
            iid = f"INC-{len(self.incidents) + 1:03d}"
        if kind == "slow":
            title = "Câu SQL chạy chậm"
            sev = "P2"
            sample = row.get("sample") or row["q"]
            symptom = (f"Giám sát phát hiện câu SQL chạy chậm trên database `{db}`: {metrics['so_lan']} lần chạy trong chu kỳ, "
                       f"tổng {metrics['tong_s']} giây, trung bình {metrics['avg_s']} giây/lần (ngưỡng {self.slow_avg} giây), "
                       f"đọc trung bình {metrics['dong_doc_tb']:,} dòng/lần. Câu SQL mẫu: {sample[:300]}")
        else:
            title = "Phiên bị treo do chờ khóa"
            sev = "P1"
            sample = row.get("waiting_query")
            symptom = (f"Giám sát phát hiện một phiên đang chờ khóa {metrics['cho_giay']} giây trên bảng {metrics['bang']} "
                       f"(ngưỡng {self.lock_age} giây). Lệnh đang bị treo: {sample}")
        inc = {"id": iid, "kind": kind, "db": db, "title": title, "severity": sev, "status": "new",
               "created": time.strftime("%H:%M:%S"), "symptom": symptom, "sample_sql": sample, "metrics": metrics,
               "trace": [], "final": None, "chat": [], "action": None, "verify": None,
               "ctx": {"db": db, "symptom": symptom, "key": None}}
        with self.lock:
            self.incidents.append(inc)
        self._log(f"🚨 Tạo {iid} [{sev}] {title} – {db}")
        if self.auto and self.llms:
            self.diagnose_async(inc)
        return inc

    def get(self, iid):
        with self.lock:
            return next((i for i in self.incidents if i["id"] == iid), None)

    def _emit_to(self, inc):
        def emit(**ev):
            ev.setdefault("t", time.strftime("%H:%M:%S"))
            role = {"CPU and Workload DBA": "cpu", "Memory and Database DBA": "mem"}.get(ev.get("agent"), ev.get("agent"))
            if "model" not in ev and self.llms:
                r = {"Chief DBA": "chief", "Phản biện": "reviewer"}.get(ev.get("agent"), role)
                if r in self.llms:
                    ev["model"] = f"{self.llms[r]['provider']} · {self.llms[r]['model']}"
            with self.lock:
                inc["trace"].append(ev)
        return emit

    def _bg(self, fn, inc, busy, done):
        def run():
            inc["status"] = busy
            try:
                fn()
                inc["status"] = done(inc) if callable(done) else done
            except Exception as e:
                inc["status"] = "error"
                inc["error"] = f"{e}"
                self._log(f"{inc['id']} lỗi: {e}")
                traceback.print_exc()
        threading.Thread(target=run, daemon=True).start()

    # (3) tu chan doan
    def diagnose_async(self, inc, llms=None, max_steps=5):
        llms = llms or self.llms
        inc["trace"] = []

        def fn():
            emit = self._emit_to(inc)
            trace, final = ag.diagnose(self.cfg, inc["ctx"], llms, on_event=lambda ev: emit(**ev), max_steps=max_steps)
            inc["final"] = final
        self._bg(fn, inc, "diag", "wait")

    # (5-6) hoi them
    def ask_async(self, inc, question, llms=None):
        llms = llms or self.llms
        inc["chat"].append({"role": "DBA", "text": question, "t": time.strftime("%H:%M:%S")})
        prev = inc["status"]

        def fn():
            steps = []
            emit = self._emit_to({"trace": steps})
            ans = ag.chat_answer(self.cfg, inc, llms["chief"], question, emit=lambda **ev: emit(**ev))
            msg = {"role": "Chief DBA", "text": ans.get("answer", str(ans)), "steps": steps, "t": time.strftime("%H:%M:%S")}
            nf = (ans.get("new_fix_sql") or "").strip()
            if nf and inc.get("final") is not None:
                inc["final"]["fix_sql"] = nf
                msg["new_fix"] = nf
            inc["chat"].append(msg)
        self._bg(fn, inc, "chat", lambda i: prev if prev in ("wait", "closed", "open", "rejected") else "wait")

    # (8) duyet -> chay -> (9) kiem tra lai
    def approve_async(self, inc, fix_sql, llms=None):
        llms = llms or self.llms
        inc["verify"] = None
        inc["verify_trace"] = []

        def fn():
            self.mute = True
            try:
                self._approve_body(inc, fix_sql, llms)
            finally:
                time.sleep(self.interval + 0.5)
                self._snap = self._snapshot()
                self.mute = False

        def done(i):
            if not (i.get("action") or {}).get("ok"):
                return "wait"
            return "closed" if (i.get("verify") or {}).get("resolved") else "open"
        self._bg(fn, inc, "fixing", done)

    def _approve_body(self, inc, fix_sql, llms):
        if True:
            res = ag.apply_action(self.cfg, inc, fix_sql)
            res["sql"] = fix_sql
            inc["action"] = res
            if not res.get("ok"):
                return
            if inc["kind"] == "lock" and self.fault_lock:
                self.fault_lock.b_thread and self.fault_lock.b_thread.join(timeout=10)
            inc["status"] = "verify"
            emit = self._emit_to({"trace": inc["verify_trace"]})
            inc["verify"] = ag.verify_after(self.cfg, inc, llms["chief"], res, emit=lambda **ev: emit(**ev))

    def reject(self, inc, reason=""):
        inc["action"] = {"ok": False, "msg": f"DBA từ chối. {reason}".strip()}
        inc["status"] = "rejected"
        self._log(f"{inc['id']}: DBA từ chối đề xuất.")

    # ------------------------------------------------------------ gay su co (gia lap ung dung)
    def fault(self, cfg, kind):
        self.cfg = self.cfg or cfg

        def run():
            try:
                c = core.connect(cfg, db=None)          # moi kich ban bat dau voi so lieu giam sat sach
                try:
                    core.query(c, "CALL sys.ps_truncate_all_tables(FALSE)")
                finally:
                    c.close()
                with self.lock:
                    self._seen = {k for k in self._seen if k[0] == "lock"}
                if kind == "kb1":
                    self.fault_status = "Ứng dụng đang chạy báo cáo theo phòng ban (30 truy vấn)..."
                    c = core.connect(cfg)
                    try:
                        core.drop_secondary_indexes(c)
                    finally:
                        c.close()
                    core.kb1_workload(cfg)
                elif kind == "kb2":
                    self.fault_status = "Ứng dụng đang chạy báo cáo lương (truy vấn con)..."
                    core.kb2_run(cfg, core.KB2_SLOW)
                elif kind == "kb3":
                    self.fault_status = "Phiên A giữ khóa, phiên B đang UPDATE cùng dòng..."
                    if self.fault_lock:
                        try:
                            self.fault_lock.fix()
                        except Exception:
                            pass
                    lk = core.LockScenario(cfg, wait_timeout=600)
                    lk.start_a()
                    lk.start_b()
                    self.fault_lock = lk
                    return
                elif kind == "tpch":
                    self.fault_status = "Ứng dụng đang chạy báo cáo doanh thu TPC-H Q17..."
                    tp.q17_reset(cfg)
                    tp.q17_run(cfg, tp.Q17_SLOW)
                self.fault_status = "Ứng dụng chạy xong."
            except Exception as e:
                self.fault_status = f"Lỗi khi gây sự cố: {e}"
        threading.Thread(target=run, daemon=True).start()

    def release_lock(self):
        if self.fault_lock:
            try:
                self.fault_lock.fix()
            except Exception:
                pass
            self.fault_lock = None

    def reset(self):
        self.stop()
        self.release_lock()
        with self.lock:
            self.incidents, self.log, self._seen = [], [], set()
