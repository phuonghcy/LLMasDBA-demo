"""
app.py - Giao dien demo "LLM As DBA" (Streamlit)
Chay:  python -m streamlit run app.py
"""
import json
import os
import pandas as pd
import streamlit as st
import demo_core as core
import tpch_core as tp
import agent_core as ag
import monitor_core as mon

st.set_page_config(page_title="Demo LLM As DBA – Nhóm 3", page_icon="🩺", layout="wide")
ss = st.session_state
for k, v in {"res": {}, "answers": {}, "lock": None, "tune": [], "ai_cfgs": {}, "tp_tune": [], "tp_cfgs": {}, "ag_state": None, "ag_trace": [], "ag_final": None, "ag_fix": None}.items():
    ss.setdefault(k, v)

# =====================================================================
# SIDEBAR: ket noi MySQL + chon AI
# =====================================================================
with st.sidebar:
    st.header("⚙️ Cấu hình")
    st.subheader("MySQL")
    cfg = {"host": st.text_input("Host", "localhost"), "port": st.text_input("Port", "3306"),
           "user": st.text_input("User", "root"),
           "password": st.text_input("Password", os.getenv("MYSQL_PWD", ""), type="password")}
    st.subheader("AI")
    provider = st.radio("Nhà cung cấp", ["Claude", "Gemini", "Thủ công (copy/paste)"], horizontal=False)
    if provider == "Claude":
        api_key = st.text_input("Anthropic API key", os.getenv("ANTHROPIC_API_KEY", ""), type="password")
        model = st.text_input("Model", os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"))
    elif provider == "Gemini":
        api_key = st.text_input("Gemini API key", os.getenv("GEMINI_API_KEY", ""), type="password")
        model = st.text_input("Model", os.getenv("GEMINI_MODEL", "gemini-3.8-flash"))
    else:
        api_key, model = "", ""
    st.caption("Key chỉ nằm trong phiên chạy trên máy bạn, không lưu ra file.")

AUTO = provider in ("Claude", "Gemini")


def ai_box(kb, label, prompt, key):
    """Hien prompt + cau tra loi (goi API hoac dan tay) + cham diem tu dong."""
    with st.expander(f"📝 Xem prompt {label}", expanded=not AUTO):
        st.code(prompt, language="text")
    ans_key = f"{kb}_{key}"
    if AUTO:
        if st.button(f"🤖 Hỏi AI – {label}", key=f"btn_{ans_key}"):
            if not api_key:
                st.error("Chưa nhập API key ở thanh bên trái.")
            else:
                with st.spinner("Đang hỏi AI..."):
                    try:
                        ss.answers[ans_key] = core.ask_ai(provider.split()[0], api_key, model, prompt)
                    except Exception as e:
                        st.error(str(e))
    else:
        txt = st.text_area(f"Dán câu trả lời của ChatGPT cho {label}", ss.answers.get(ans_key, ""), key=f"ta_{ans_key}", height=150)
        ss.answers[ans_key] = txt
    ans = ss.answers.get(ans_key)
    if ans:
        if AUTO:
            st.markdown(ans)
        ok, spec = core.auto_grade(kb, ans)
        c1, c2 = st.columns(2)
        ok = c1.checkbox("Đúng nguyên nhân", ok, key=f"ok_{ans_key}")
        spec = c2.checkbox("Có lệnh sửa cụ thể", spec, key=f"sp_{ans_key}")
        ss.res.setdefault("grades", {})[ans_key] = (ok, spec)


def compare_chart(before, after, labels=("① Trước khi sửa", "② Sau khi sửa")):
    st.bar_chart(pd.DataFrame({"giây": [before, after]}, index=list(labels)), y="giây", color="#E4572E")


# =====================================================================
# TRANG CHINH
# =====================================================================
st.title("🩺 Demo LLM As DBA – AI chẩn đoán & tối ưu MySQL")
st.caption("Paper 5.2 D-Bot (chẩn đoán sự cố) • Paper 5.5 λ-Tune (đề xuất cấu hình rồi đo thật) • Nhóm 3 – Hệ CSDL tiên tiến")

tabs = st.tabs(["0️⃣ Chuẩn bị", "1️⃣ Thiếu index", "2️⃣ Truy vấn con", "3️⃣ Tranh chấp khóa", "4️⃣ Mini λ-Tune", "5️⃣ TPC-H", "🤖 D-Bot tự chủ", "📊 Tổng kết"])

# ---------------- TAB 0 ----------------
with tabs[0]:
    st.subheader("Bước 0 – Kiểm tra kết nối & tạo dữ liệu")
    st.markdown("Tạo CSDL `demo_dbot`: bảng **emp** 1.000.000 nhân viên (giống EMPLOYEE của COMPANY), **dept** 100 phòng, **emp_small** 5.000 dòng.")
    c1, c2 = st.columns(2)
    if c1.button("🔌 Kiểm tra kết nối"):
        try:
            st.success(f"Kết nối OK. CSDL demo_dbot {'đã có' if core.db_exists(cfg) else 'CHƯA có – bấm Tạo dữ liệu'}.")
        except Exception as e:
            st.error(f"Không kết nối được MySQL: {e}")
    n_small = c2.number_input("Số dòng emp_small (kịch bản 2)", 2000, 10000, 5000, 1000,
                              help="Máy chậm → giảm; máy nhanh → tăng, sao cho truy vấn chậm mất 5–30 giây")
    if st.button("🏗️ Tạo dữ liệu (≈10–60 giây)", type="primary"):
        with st.spinner("Đang tạo 1 triệu dòng..."):
            try:
                core.setup_database(cfg, n_small=int(n_small))
                st.success(f"Xong: {core.table_counts(cfg)}")
            except Exception as e:
                st.error(str(e))

# ---------------- TAB 1 ----------------
with tabs[1]:
    st.subheader("Kịch bản 1 – MISSING_INDEXES")
    st.caption("Table 1 của paper 5.2 • Liên hệ: Ch.17 slide 34 / sách tr. 640 (Tuning Indexes)")
    st.markdown("**① Tạo sự cố:** xóa index, ứng dụng gọi 30 truy vấn `SELECT COUNT(*), AVG(salary) FROM emp WHERE dno = ?`")
    if st.button("▶️ Chạy workload (chưa có index)", key="kb1_run"):
        with st.spinner("Đang chạy 30 truy vấn..."):
            core.kb1_break(cfg)
            ss.res["kb1_before"] = core.kb1_workload(cfg)
    if "kb1_before" in ss.res:
        st.metric("Thời gian 30 truy vấn", f"{ss.res['kb1_before']:.2f} giây")
        st.markdown("**② Công cụ đo (kiểu D-Bot):** EXPLAIN + `sys.statements_with_full_table_scans` + `sys.innodb_lock_waits`")
        if st.button("🔍 Thu thập công cụ", key="kb1_tool"):
            ss.res["kb1_tools"] = core.kb1_tools(cfg)
    if "kb1_tools" in ss.res:
        t = ss.res["kb1_tools"]
        st.dataframe(pd.DataFrame(t["explain"]), width="stretch")
        st.dataframe(pd.DataFrame(t["full_scans"]), width="stretch")
        st.write(f"Số dòng trong sys.innodb_lock_waits: **{t['lock_waits']}**")
        symptom = (f"Mot chuc nang bao cao goi 30 truy van lien tiep mat {ss.res['kb1_before']:.1f} giay, "
                   "CPU server tang cao trong luc chay. Bang emp co 1.000.000 dong.")
        tool_text = (f"EXPLAIN {core.KB1_EXPLAIN}:\n{core.fmt_rows(t['explain'], ['select_type','table','type','possible_keys','key','rows','Extra'])}\n"
                     f"sys.statements_with_full_table_scans:\n{core.fmt_rows(t['full_scans'])}\n"
                     f"sys.innodb_lock_waits: {t['lock_waits']} dong")
        st.markdown("**③ Hỏi AI theo 2 cách**")
        a, b = st.columns(2)
        with a:
            st.markdown("#### 🅰️ AI trơn (chỉ triệu chứng)")
            ai_box("kb1", "A", core.prompt_a(symptom), "A")
        with b:
            st.markdown("#### 🅱️ AI kiểu D-Bot (kinh nghiệm + công cụ)")
            ai_box("kb1", "B", core.prompt_b(symptom, tool_text), "B")
        st.markdown("**④ Sửa theo đề xuất:** `CREATE INDEX idx_dno ON emp(dno)` rồi chạy lại")
        if st.button("🛠️ Tạo index & chạy lại", key="kb1_fix"):
            with st.spinner("Đang tạo index..."):
                t_idx, exp2 = core.kb1_fix(cfg)
                ss.res["kb1_after"] = core.kb1_workload(cfg)
                ss.res["kb1_exp2"] = exp2
    if "kb1_after" in ss.res:
        st.dataframe(pd.DataFrame(ss.res["kb1_exp2"]), width="stretch")
        compare_chart(ss.res["kb1_before"], ss.res["kb1_after"])
        st.success(f"{ss.res['kb1_before']:.2f}s → {ss.res['kb1_after']:.2f}s (nhanh hơn {ss.res['kb1_before']/ss.res['kb1_after']:.1f} lần)")

# ---------------- TAB 2 ----------------
with tabs[2]:
    st.subheader("Kịch bản 2 – CORRELATED_SUBQUERY")
    st.caption("Ví dụ chính trong mục 9 của paper 5.2 • Liên hệ: Ch.19 slide 17 / sách tr. 704 (Unnesting)")
    st.markdown("**① Tạo sự cố:** nhân viên có lương cao hơn trung bình phòng mình")
    st.code(core.KB2_SLOW, language="sql")
    if st.button("▶️ Chạy truy vấn chậm", key="kb2_run"):
        with st.spinner("Đang chạy (có thể 5–30 giây)..."):
            ss.res["kb2_before"] = core.kb2_run(cfg, core.KB2_SLOW)
    if "kb2_before" in ss.res:
        t, n, exp = ss.res["kb2_before"]
        st.metric("Thời gian", f"{t:.2f} giây", help=f"Kết quả COUNT = {n}")
        st.markdown("**② Công cụ đo:** EXPLAIN")
        st.dataframe(pd.DataFrame(exp), width="stretch")
        symptom = f"Mot bao cao luong chay mat {t:.1f} giay du bang chi co vai nghin dong, CPU cao."
        tool_text = (f"Cau SQL:\n{core.KB2_SLOW}\nEXPLAIN:\n{core.fmt_rows(exp, ['id','select_type','table','type','rows','Extra'])}\n"
                     "sys.innodb_lock_waits: 0 dong")
        a, b = st.columns(2)
        with a:
            st.markdown("#### 🅰️ AI trơn")
            ai_box("kb2", "A", core.prompt_a(symptom), "A")
        with b:
            st.markdown("#### 🅱️ AI kiểu D-Bot")
            ai_box("kb2", "B", core.prompt_b(symptom, tool_text), "B")
        st.markdown("**④ Sửa – viết lại (unnesting):**")
        st.code(core.KB2_FAST, language="sql")
        if st.button("🛠️ Chạy bản viết lại", key="kb2_fix"):
            ss.res["kb2_after"] = core.kb2_run(cfg, core.KB2_FAST)
    if "kb2_after" in ss.res:
        t2, n2, exp2 = ss.res["kb2_after"]
        st.dataframe(pd.DataFrame(exp2), width="stretch")
        compare_chart(ss.res["kb2_before"][0], t2)
        st.success(f"{ss.res['kb2_before'][0]:.2f}s → {t2:.3f}s, cùng kết quả COUNT = {n2} "
                   f"(nhanh hơn {ss.res['kb2_before'][0]/max(t2,1e-4):.0f} lần)")

# ---------------- TAB 3 ----------------
with tabs[3]:
    st.subheader("Kịch bản 3 – LOCK_CONTENTION")
    st.caption("Nhóm 'Concurrent Transaction' trong Table 1 • Liên hệ: Ch.21 slide 13 (Strict 2PL) / sách tr. 789")
    st.markdown("App mở **3 kết nối**: **A** giữ khóa (chưa COMMIT) • **B** cập nhật cùng dòng → bị chặn • **C** quan sát `sys.innodb_lock_waits`")
    c1, c2 = st.columns(2)
    if c1.button("▶️ ① Phiên A: START TRANSACTION + UPDATE (chưa commit)", key="kb3_a"):
        ss.lock = core.LockScenario(cfg)
        ss.res["kb3_pid"] = ss.lock.start_a()
    if ss.lock and c2.button("▶️ ② Phiên B: UPDATE cùng dòng", key="kb3_b"):
        ss.lock.start_b()
    if ss.lock:
        st.info(f"Phiên A (pid {ss.res.get('kb3_pid')}) đang giữ khóa. Phiên B: **{ss.lock.b_result.get('status')}**")
        if st.button("🔍 ③ Thu thập công cụ (phiên C)", key="kb3_tool"):
            ss.res["kb3_tools"] = ss.lock.tools()
    if "kb3_tools" in ss.res:
        st.dataframe(pd.DataFrame(ss.res["kb3_tools"]), width="stretch")
        symptom = ("Mot lenh UPDATE rat don gian (UPDATE emp SET salary = salary + 2 WHERE id = 1) bi treo, "
                   "sau mot luc bao loi 'Lock wait timeout exceeded'. CPU va disk deu thap.")
        tool_text = f"sys.innodb_lock_waits:\n{core.fmt_rows(ss.res['kb3_tools'])}"
        a, b = st.columns(2)
        with a:
            st.markdown("#### 🅰️ AI trơn")
            ai_box("kb3", "A", core.prompt_a(symptom), "A")
        with b:
            st.markdown("#### 🅱️ AI kiểu D-Bot")
            ai_box("kb3", "B", core.prompt_b(symptom, tool_text), "B")
    if ss.lock and st.button("🛠️ ④ Sửa: phiên A ROLLBACK", key="kb3_fix"):
        r = ss.lock.fix()
        ss.res["kb3_after"] = r
        ss.lock = None
    if "kb3_after" in ss.res:
        r = ss.res["kb3_after"]
        st.success(f"Phiên B: {r.get('status')} sau {r.get('seconds', 0):.1f} giây chờ khóa {r.get('error','')}")

# ---------------- TAB 4 ----------------
with tabs[4]:
    st.subheader("Mini λ-Tune – AI đề xuất cấu hình, máy ĐO THẬT rồi chọn")
    st.caption("Paper 5.5 • Liên hệ: Ch.17 slide 40 (index trên thuộc tính nào), Ch.16 (buffer), Ch.19 slide 24 (chi phí truy cập đĩa)")
    st.markdown("Workload OLAP 3 truy vấn, mỗi truy vấn có **timeout** bằng hint `MAX_EXECUTION_TIME` (giống λ-Tune):")
    st.dataframe(pd.DataFrame([{"truy_van": n, "sql": q.format(to=10000)} for n, q in core.LT_WORKLOAD]), width="stretch")
    c1, c2, c3 = st.columns(3)
    mem = c1.text_input("RAM máy (đưa vào prompt)", "8GB")
    cores_ = c2.text_input("Số CPU", "4")
    k = c3.number_input("Số cấu hình xin AI (k)", 1, 5, 3)
    prompt = core.LT_PROMPT.replace("{mem}", mem).replace("{cores}", cores_)
    with st.expander("📝 Prompt theo mẫu Listing 1 của λ-Tune"):
        st.code(prompt, language="text")

    if AUTO and st.button(f"🤖 Xin AI {k} cấu hình", key="lt_ask"):
        ss.ai_cfgs = {}
        for i in range(int(k)):
            with st.spinner(f"Cấu hình AI-{i+1}..."):
                try:
                    txt = core.ask_ai(provider.split()[0], api_key, model, prompt, temperature=1.0)
                    ok, bad = core.parse_config(txt)
                    ss.ai_cfgs[f"AI-{i+1}"] = {"ok": ok, "bad": bad}
                except Exception as e:
                    st.error(str(e))
                    break
    if not AUTO:
        pasted = st.text_area("Dán cấu hình ChatGPT trả về (mỗi lần hỏi 1 ô, cách nhau bằng dòng ===)", height=150)
        if st.button("📥 Đọc cấu hình đã dán", key="lt_parse"):
            ss.ai_cfgs = {}
            for i, part in enumerate([p for p in pasted.split("===") if p.strip()]):
                ok, bad = core.parse_config(part)
                ss.ai_cfgs[f"AI-{i+1}"] = {"ok": ok, "bad": bad}
    for name, c in ss.ai_cfgs.items():
        st.markdown(f"**{name}** – lệnh được chạy:")
        st.code(";\n".join(c["ok"]) or "(không có lệnh hợp lệ)", language="sql")
        if c["bad"]:
            st.warning(f"Đã chặn {len(c['bad'])} lệnh không nằm trong danh sách cho phép (giống bước DBA phê duyệt): {c['bad']}")

    use_presets = st.checkbox("Thêm 3 cấu hình mẫu (C1–C3) để đối chiếu", value=True)
    if st.button("⏱️ Chạy thật & đo tất cả cấu hình", type="primary", key="lt_run"):
        configs = {"C0 mặc định": []}
        if use_presets:
            configs.update(core.PRESETS)
        configs.update({n: c["ok"] for n, c in ss.ai_cfgs.items()})
        ss.tune = []
        bar = st.progress(0.0)
        for i, (n, stmts) in enumerate(configs.items()):
            with st.spinner(f"Đang đo {n}..."):
                ss.tune.append(core.run_config(cfg, n, stmts))
            bar.progress((i + 1) / len(configs))
        core.cleanup_after_tune(cfg)
    if ss.tune:
        df = pd.DataFrame([{k2: v for k2, v in r.items() if k2 in ("cau_hinh", "giay_workload", "giay_ap_dung", "bi_timeout")} for r in ss.tune])
        st.bar_chart(df.set_index("cau_hinh"), y="giay_workload", color="#F3A712")
        st.dataframe(df, width="stretch")
        best = min(ss.tune, key=lambda r: r["giay_workload"])
        worst = max(ss.tune, key=lambda r: r["giay_workload"])
        st.success(f"✅ Chọn **{best['cau_hinh']}**: {best['giay_workload']}s. Tệ nhất: {worst['cau_hinh']} "
                   f"({worst['giay_workload']}s, chậm gấp {worst['giay_workload']/max(best['giay_workload'],1e-3):.1f} lần) "
                   "→ vì vậy λ-Tune phải đo thật, không tin lời AI.")
        for r in ss.tune:
            if r.get("loi"):
                st.warning(f"{r['cau_hinh']}: lệnh lỗi khi áp dụng {r['loi']}")

# ---------------- TAB 5: TPC-H ----------------
with tabs[5]:
    st.subheader("TPC-H – benchmark thật mà paper 5.5 λ-Tune dùng")
    st.caption("TPC-H: 8 bảng bán hàng (lineitem, orders, customer, part…) + 22 truy vấn chuẩn. "
               "λ-Tune đo trên TPC-H 1GB/10GB; ở đây dùng bản nhỏ để chạy kịp trên laptop.")

    # ---- A. Tao du lieu ----
    st.markdown("### Ⓐ Tạo dữ liệu TPC-H")
    c1, c2 = st.columns([1, 2])
    sf = c1.selectbox("Scale factor (SF)", [0.02, 0.05, 0.1], index=1,
                      help="0.05 ≈ 50MB, lineitem 300 nghìn dòng, Q17 chậm ≈ 10 giây (khuyên dùng). 0.1 → Q17 ≈ 1 phút.")
    c2.markdown("Sinh dữ liệu bằng **tpchgen-cli** (bộ sinh dữ liệu chuẩn TPC-H) → nạp vào database `tpch` "
                "bằng `LOAD DATA`. Chỉ có PRIMARY KEY, **chưa có index phụ**.")
    b1, b2 = st.columns(2)
    if b1.button("🏗️ Tạo TPC-H (≈10–30 giây)", type="primary", key="tp_setup"):
        logs = st.empty()
        lines = []
        def _log(m):
            lines.append(m)
            logs.code("\n".join(lines))
        try:
            ss.res["tp_counts"] = tp.setup_tpch(cfg, sf, log=_log)
        except Exception as e:
            st.error(str(e))
    if b2.button("🔌 Kiểm tra database tpch", key="tp_check"):
        try:
            ss.res["tp_counts"] = tp.table_counts(cfg) if tp.db_exists(cfg) else None
            if not ss.res["tp_counts"]:
                st.warning("Chưa có database tpch – bấm Tạo TPC-H.")
        except Exception as e:
            st.error(str(e))
    if ss.res.get("tp_counts"):
        st.dataframe(pd.DataFrame([ss.res["tp_counts"]]), width="stretch")

    # ---- B. D-Bot tren Q17 ----
    st.markdown("### Ⓑ D-Bot trên TPC-H Q17 – truy vấn con tương quan có sẵn trong benchmark")
    st.caption("Đúng loại CORRELATED_SUBQUERY trong Table 1 của paper 5.2 • Ch.19 slide 17 (unnesting) • Ch.17 (index)")
    st.code(tp.Q17_SLOW, language="sql")
    if st.button("▶️ ① Chạy Q17 (chưa có index)", key="tp_q17"):
        with st.spinner("Đang chạy Q17 (≈10 giây với SF 0.05)..."):
            tp.q17_reset(cfg)
            ss.res["tp_slow"] = tp.q17_run(cfg, tp.Q17_SLOW)
    if "tp_slow" in ss.res:
        t, v, exp = ss.res["tp_slow"]
        st.metric("Q17 – thời gian", f"{t:.2f} giây", help=f"avg_yearly = {v}")
        st.markdown("**② Công cụ đo:** EXPLAIN")
        st.dataframe(pd.DataFrame(exp), width="stretch")
        symptom = (f"Bao cao doanh thu TPC-H Q17 chay mat {t:.1f} giay, CPU cao. "
                   "Bang lineitem co vai tram nghin dong, bang part 10 nghin dong.")
        tool_text = (f"Cau SQL:\n{tp.Q17_SLOW}\nEXPLAIN:\n"
                     f"{core.fmt_rows(exp, ['id','select_type','table','type','key','rows','Extra'])}\n"
                     "Index hien co: chi PRIMARY KEY.\nsys.innodb_lock_waits: 0 dong")
        st.markdown("**③ Hỏi AI theo 2 cách**")
        a, b = st.columns(2)
        with a:
            st.markdown("#### 🅰️ AI trơn")
            ai_box("tpch", "A", core.prompt_a(symptom), "A")
        with b:
            st.markdown("#### 🅱️ AI kiểu D-Bot")
            ai_box("tpch", "B", core.prompt_b(symptom, tool_text), "B")
        st.markdown("**④ Sửa – 2 cách**")
        f1, f2 = st.columns(2)
        with f1:
            st.markdown("**Cách 1 – viết lại (unnesting)**")
            st.code(tp.Q17_FAST, language="sql")
            if st.button("🛠️ Chạy bản viết lại", key="tp_fast"):
                ss.res["tp_fast"] = tp.q17_run(cfg, tp.Q17_FAST)
        with f2:
            st.markdown("**Cách 2 – thêm index trên khóa JOIN**")
            st.code(tp.Q17_INDEX, language="sql")
            if st.button("🛠️ Tạo index & chạy lại Q17 gốc", key="tp_idx"):
                with st.spinner("Đang tạo index..."):
                    tp.q17_add_index(cfg)
                    ss.res["tp_idx"] = tp.q17_run(cfg, tp.Q17_SLOW)
                    tp.q17_reset(cfg)
    if "tp_slow" in ss.res and ("tp_fast" in ss.res or "tp_idx" in ss.res):
        bars = {"① Q17 gốc": ss.res["tp_slow"][0]}
        if "tp_fast" in ss.res:
            bars["② Viết lại (unnesting)"] = ss.res["tp_fast"][0]
        if "tp_idx" in ss.res:
            bars["③ Index l_partkey"] = ss.res["tp_idx"][0]
        st.bar_chart(pd.DataFrame({"giây": list(bars.values())}, index=list(bars)), y="giây", color="#E4572E")
        st.success(" • ".join(f"{k}: {v:.3f}s" for k, v in bars.items()) +
                   f" — cùng kết quả avg_yearly = {ss.res['tp_slow'][1]}")

    # ---- C. Mini lambda-Tune tren TPC-H ----
    st.markdown("### Ⓒ Mini λ-Tune trên TPC-H – AI đề xuất, máy đo thật")
    st.markdown("Workload 5 truy vấn TPC-H nhiều phép JOIN: **Q3, Q5, Q10, Q12, Q17**, mỗi truy vấn có timeout (`MAX_EXECUTION_TIME`).")
    c1, c2, c3, c4 = st.columns(4)
    mem_t = c1.text_input("RAM máy", "8GB", key="tp_mem")
    cores_t = c2.text_input("Số CPU", "4", key="tp_cores")
    k_t = c3.number_input("Số cấu hình xin AI", 1, 5, 3, key="tp_k")
    to_t = c4.number_input("Timeout mỗi truy vấn (giây)", 5, 60, 10, key="tp_to")
    if ss.res.get("tp_counts"):
        tprompt = tp.tpch_prompt(cfg, mem_t, cores_t)
        with st.expander("📝 Prompt theo mẫu Listing 1 của λ-Tune (workload đã nén thành các phép JOIN)"):
            st.code(tprompt, language="text")
        if AUTO and st.button(f"🤖 Xin AI {k_t} cấu hình cho TPC-H", key="tp_ask"):
            ss.tp_cfgs = {}
            for i in range(int(k_t)):
                with st.spinner(f"Cấu hình AI-{i+1}..."):
                    try:
                        txt = core.ask_ai(provider.split()[0], api_key, model, tprompt, temperature=1.0)
                        ok, bad = tp.parse_config(txt)
                        ss.tp_cfgs[f"AI-{i+1}"] = {"ok": ok, "bad": bad}
                    except Exception as e:
                        st.error(str(e))
                        break
        if not AUTO:
            pasted_t = st.text_area("Dán cấu hình ChatGPT trả về (mỗi lần hỏi cách nhau bằng dòng ===)", height=150, key="tp_paste")
            if st.button("📥 Đọc cấu hình đã dán", key="tp_parse"):
                ss.tp_cfgs = {}
                for i, part in enumerate([p for p in pasted_t.split("===") if p.strip()]):
                    ok, bad = tp.parse_config(part)
                    ss.tp_cfgs[f"AI-{i+1}"] = {"ok": ok, "bad": bad}
    else:
        st.info("Tạo hoặc kiểm tra database tpch ở mục Ⓐ trước.")
    for name, c in ss.tp_cfgs.items():
        st.markdown(f"**{name}** – lệnh được chạy:")
        st.code(";\n".join(c["ok"]) or "(không có lệnh hợp lệ)", language="sql")
        if c["bad"]:
            st.warning(f"Đã chặn {len(c['bad'])} lệnh ngoài danh sách cho phép (DBA phê duyệt): {c['bad']}")
    use_p = st.checkbox("Thêm 3 cấu hình mẫu (C1 buffer, C2 index ngày, C3 index khóa JOIN)", value=True, key="tp_presets")
    if st.button("⏱️ Chạy thật & đo tất cả cấu hình", type="primary", key="tp_run"):
        configs = {"C0 mặc định": []}
        if use_p:
            configs.update(tp.TPCH_PRESETS)
        configs.update({n: c["ok"] for n, c in ss.tp_cfgs.items()})
        ss.tp_tune = []
        bar = st.progress(0.0)
        for i, (n, stmts) in enumerate(configs.items()):
            with st.spinner(f"Đang đo {n}..."):
                ss.tp_tune.append(tp.run_config(cfg, n, stmts, timeout_ms=int(to_t) * 1000))
            bar.progress((i + 1) / len(configs))
        tp.cleanup(cfg)
    if ss.tp_tune:
        cols = ["cau_hinh", "giay_workload", "giay_ap_dung", "so_timeout"] + [q for q, _ in tp.TPCH_WORKLOAD]
        df = pd.DataFrame([{c: r.get(c) for c in cols} for r in ss.tp_tune])
        st.bar_chart(df.set_index("cau_hinh"), y="giay_workload", color="#F3A712")
        st.dataframe(df, width="stretch")
        best = min(ss.tp_tune, key=lambda r: r["giay_workload"])
        worst = max(ss.tp_tune, key=lambda r: r["giay_workload"])
        st.success(f"✅ Chọn **{best['cau_hinh']}**: {best['giay_workload']}s. Tệ nhất: {worst['cau_hinh']} "
                   f"({worst['giay_workload']}s, chậm gấp {worst['giay_workload']/max(best['giay_workload'],1e-3):.0f} lần). "
                   "Cấu hình nghe hợp lý chưa chắc nhanh → λ-Tune phải đo thật.")
        for r in ss.tp_tune:
            st.caption(f"{r['cau_hinh']}: {'; '.join(r['lenh']) or '(không đổi gì)'}")
            if r.get("loi"):
                st.warning(f"{r['cau_hinh']}: lệnh lỗi {r['loi']}")

# ---------------- TAB 6: D-BOT TU CHU (giam sat -> incident -> hoi chan -> chat -> duyet -> kiem tra) ----------------
AVATAR = {"Hệ thống": "📟", "Chief DBA": "👨‍⚕️", "cpu": "⚙️", "mem": "🧠", "Phản biện": "⚖️"}
AGNAME = {"cpu": "CPU and Workload DBA", "mem": "Memory and Database DBA"}
BUSY = ("new", "diag", "chat", "fixing", "verify")


@st.cache_resource
def get_hub():
    return mon.Hub()


hub = get_hub()


def render_event(ev):
    name = AGNAME.get(ev["agent"], ev["agent"])
    with st.chat_message(name, avatar=AVATAR.get(ev["agent"], "🤖")):
        st.caption(f"**{name}**" + (f" · `{ev['model']}`" if ev.get("model") else "") + f" · {ev.get('t','')}")
        k = ev["kind"]
        if k == "input":
            st.markdown(f"🚨 **Nhận incident (chưa có bằng chứng):** {ev['text']}")
        elif k == "plan":
            st.markdown(f"💭 {ev['text']}")
            st.markdown("📋 **Giao việc cho:** " + " → ".join(AGNAME.get(a, a) for a in ev["assign"]))
        elif k == "thought":
            st.markdown(f"💭 {ev['text']}")
        elif k == "tool":
            st.markdown(f"🛠️ Tự chọn công cụ **`{ev['tool']}`** `{json.dumps(ev['args'], ensure_ascii=False)}`")
            st.code(ev["text"], language="text")
        elif k == "backtrack":
            st.warning("↩️ Quay lại: bước này không cho thông tin hữu ích → thử hướng khác")
        elif k == "report":
            st.info(f"📄 **Báo cáo:** {ev['text']}\n\n**Bằng chứng:** {ev.get('evidence','')}\n\n**Đề xuất:** `{ev.get('fix_sql','') or '—'}`")
        elif k in ("draft", "revise"):
            st.markdown(("📝 **Kết luận nháp:** " if k == "draft" else "🔁 **Xem lại sau phản biện:** ") + ev["text"])
            if ev.get("fix_sql"):
                st.code(ev["fix_sql"], language="sql")
        elif k == "review":
            (st.success if ev.get("agree") else st.error)(("✅ Đồng ý. " if ev.get("agree") else "❌ Không đồng ý. ") + ev["text"])
        elif k == "final":
            st.success(f"🏁 **KẾT LUẬN:** {ev['text']}")


with tabs[6]:
    st.subheader("🤖 D-Bot tự chủ – giám sát, tạo incident, nhiều AI hội chẩn")
    st.markdown("**Luồng 9 bước:** ① giám sát realtime phát hiện → ② tự tạo incident → ③ tự chẩn đoán (nhiều AI) → "
                "④ tóm tắt + bằng chứng → ⑤ DBA hỏi thêm qua chat → ⑥ AI tự gọi thêm công cụ → ⑦ đề xuất lệnh → "
                "⑧ DBA duyệt / từ chối → ⑨ AI tự kiểm tra lại và đóng incident.")

    # ---------- A. Ket noi AI ----------
    with st.expander("🔌 A. Kết nối AI – chọn hãng và model cho từng vai", expanded=not hub.running):
        provs = list(ag.PROVIDERS)
        st.caption("3 agent đúng như paper 5.2 (Mục 8): **Chief DBA** điều phối • **CPU and Workload DBA** (CPU, câu SQL nặng, "
                   "kế hoạch thực thi) • **Memory and Database DBA** (bộ nhớ, bảng/index, khóa và giao tác).")
        use_rev = st.checkbox("Thêm vai ⚖️ Phản biện (mở rộng của nhóm – paper không có)", False, key="ag_use_rev")
        defaults = {"chief": "Gemini", "cpu": "Gemini", "mem": "Gemini", "reviewer": "Gemini"}
        role_lbl = {"chief": "👨‍⚕️ Chief DBA", "cpu": "⚙️ CPU and Workload DBA", "mem": "🧠 Memory and Database DBA"}
        if use_rev:
            role_lbl["reviewer"] = "⚖️ Phản biện"
        sel = {}
        cols = st.columns(len(role_lbl))
        for col, r in zip(cols, role_lbl):
            p = col.selectbox(role_lbl[r], provs, provs.index(defaults[r]), key=f"ag_p_{r}")
            m = col.text_input("Model", ag.PROVIDERS[p][0], key=f"ag_m_{r}_{p}")
            sel[r] = (p, m)
        used = sorted({p for p, _ in sel.values()})
        keys = {}
        need = [p for p in used if p in ag.NEEDS_KEY]
        if need:
            st.markdown("**🔑 Key cho các hãng đang dùng** (chỉ nằm trong phiên, không lưu ra file):")
            kc = st.columns(min(3, len(need)))
            for i, p in enumerate(need):
                env = ag.PROVIDERS[p][1]
                dflt = api_key if ((p == "Gemini" and provider == "Gemini") or (p == "Claude API" and provider == "Claude")) else os.getenv(env, "")
                keys[p] = kc[i % len(kc)].text_input(p, dflt, type="password", key=f"ag_k_{p}")
        if "Claude Code (gói Pro)" in used:
            st.caption("Claude Code dùng gói Pro đã đăng nhập trên máy (cài: `npm install -g @anthropic-ai/claude-code`, gõ `claude` để đăng nhập). "
                       "Không đặt biến ANTHROPIC_API_KEY, nếu không sẽ bị tính tiền API.")
        if "Ollama (chạy trên máy)" in used:
            st.caption("Ollama: cài từ ollama.com rồi chạy `ollama pull qwen2.5:7b`.")
        st.caption("Gợi ý miễn phí: Chief DBA = Claude Code (gói Pro), CPU and Workload = Gemini, Memory and Database = Groq (hoặc GitHub Models).")
        llms = {r: {"provider": p, "model": m, "key": keys.get(p, "")} for r, (p, m) in sel.items()}
        miss = [p for p in need if not keys.get(p)]
        if st.button("🧪 Thử kết nối các AI", key="ag_test"):
            for (p, m) in sorted({(v["provider"], v["model"]) for v in llms.values()}):
                try:
                    out = ag.llm_call({"provider": p, "model": m, "key": keys.get(p, "")}, "Tra loi dung 1 chu: OK")
                    st.success(f"{p} · {m}: {out.strip()[:40]}")
                except Exception as e:
                    st.error(f"{p} · {m}: {e}")

    # ---------- B. Giam sat ----------
    st.markdown("### B. Giám sát realtime")
    g1, g2, g3, g4 = st.columns(4)
    interval = g1.number_input("Chu kỳ đọc số liệu (giây)", 1.0, 30.0, 3.0, 1.0, key="ag_int")
    hub.slow_avg = g2.number_input("Ngưỡng chậm (giây/lần)", 0.1, 30.0, 1.0, 0.1, key="ag_slow")
    hub.lock_age = g3.number_input("Ngưỡng chờ khóa (giây)", 1, 120, 5, 1, key="ag_lock")
    auto = g4.checkbox("Tự động chẩn đoán khi có incident", True, key="ag_auto")
    b1, b2, b3 = st.columns(3)
    if not hub.running:
        if b1.button("▶️ Bật giám sát", type="primary", key="ag_start"):
            if miss:
                st.error(f"Chưa nhập key cho: {', '.join(miss)}")
            else:
                hub.start(cfg, llms, auto=auto, interval=interval)
                st.rerun()
    else:
        hub.llms, hub.auto, hub.interval = llms, auto, interval
        if b1.button("⏹️ Tắt giám sát", key="ag_stop"):
            hub.stop()
            st.rerun()
    if b3.button("🗑️ Xóa toàn bộ incident", key="ag_reset"):
        hub.reset()
        st.rerun()

    # ---------- C. Gia lap su co ----------
    st.markdown("### C. Giả lập sự cố (ứng dụng chạy bình thường, D-Bot không được báo trước)")
    st.caption("Ca 1–3 cần dữ liệu tab 0; ca 4 cần dữ liệu tab 5. Bấm xong, chờ giám sát tự phát hiện.")
    f1, f2, f3, f4, f5 = st.columns(5)
    if f1.button("📊 Ca 1: thiếu index", key="ag_f1"):
        hub.fault(cfg, "kb1")
    if f2.button("💰 Ca 2: truy vấn con", key="ag_f2"):
        hub.fault(cfg, "kb2")
    if f3.button("🔒 Ca 3: tranh chấp khóa", key="ag_f3"):
        hub.fault(cfg, "kb3")
    if f4.button("📦 Ca 4: TPC-H Q17", key="ag_f4"):
        hub.fault(cfg, "tpch")
    if f5.button("🔓 Nhả khóa ca 3", key="ag_f5"):
        hub.release_lock()

    # ---------- D. Bang dieu khien song ----------
    st.markdown("### D. Incident")
    live = hub.running or any(i["status"] in BUSY for i in hub.incidents)

    @st.fragment(run_every=2 if live else None)
    def live_panel():
        sig = [(i["id"], i["status"], len(i["trace"]), len(i["chat"]), len(i.get("verify_trace", []))) for i in hub.incidents]
        if ss.get("ag_sig") is not None and sig != ss.ag_sig and [s[:2] for s in sig] != [s[:2] for s in ss.ag_sig]:
            ss.ag_sig = sig
            st.rerun()
        ss.ag_sig = sig
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Giám sát", "🟢 Đang chạy" if hub.running else "⚪ Tắt", help=f"Lần đọc cuối: {hub.last_poll}")
        m2.metric("Phiên đang chờ khóa", hub.last_metrics.get("phien_cho_khoa", "—"))
        m3.metric("Incident đang mở", sum(1 for i in hub.incidents if i["status"] not in ("closed", "rejected")))
        m4.metric("Đã đóng", sum(1 for i in hub.incidents if i["status"] == "closed"))
        if hub.fault_status:
            st.caption(f"Ứng dụng: {hub.fault_status}")
        if hub.incidents:
            st.dataframe(pd.DataFrame([{"Mã": i["id"], "Lúc": i["created"], "Mức": i["severity"], "Loại": i["title"],
                                        "Database": i["db"], "Trạng thái": mon.STATUS[i["status"]],
                                        "Nguyên nhân": (i.get("final") or {}).get("cause", "")[:80]} for i in reversed(hub.incidents)]),
                         width="stretch", hide_index=True)
        else:
            st.info("Chưa có incident. Bật giám sát rồi giả lập một sự cố ở mục C.")
        with st.expander("📜 Nhật ký giám sát"):
            st.code("\n".join(hub.log[-15:]) or "(trống)", language="text")
        inc = hub.get(ss.get("ag_sel")) if ss.get("ag_sel") else None
        if inc and inc["status"] in ("diag",):
            st.markdown(f"#### 🩺 {inc['id']} đang được hội chẩn… ({len(inc['trace'])} bước)")
            for ev in inc["trace"][-6:]:
                render_event(ev)
    live_panel()

    if hub.incidents:
        ids = [i["id"] for i in reversed(hub.incidents)]
        if ss.get("ag_sel") not in ids or ss.get("ag_n") != len(ids):
            ss.ag_sel, ss.ag_n = ids[0], len(ids)
        sid = st.selectbox("Xem chi tiết incident", ids, ids.index(ss.ag_sel), key="ag_selbox")
        ss.ag_sel = sid
        inc = hub.get(sid)
        busy = inc["status"] in BUSY
        st.markdown(f"## {inc['id']} · {inc['severity']} · {inc['title']} · {mon.STATUS[inc['status']]}")
        if inc.get("error"):
            st.error(inc["error"])

        # (2) incident + (4) tom tat
        st.markdown("**① ② Giám sát phát hiện & tạo incident**")
        st.warning(f"🚨 {inc['symptom']}")
        fin = inc.get("final")
        if fin:
            st.markdown("**④ Tóm tắt chẩn đoán**")
            s1, s2 = st.columns([3, 2])
            s1.success(f"**Nguyên nhân gốc:** {fin.get('cause','')}\n\n**Bằng chứng:** {fin.get('evidence','')}")
            s2.info(f"**Lệnh đề xuất:**\n\n`{fin.get('fix_sql','') or '—'}`\n\n**Rủi ro:** {fin.get('risk','')}")
        elif inc["status"] == "new" or inc["status"] == "error":
            if st.button("🩺 ③ Chạy chẩn đoán", key=f"ag_diag_{sid}", disabled=bool(miss)):
                hub.diagnose_async(inc, llms)
                st.rerun()
        if inc["trace"]:
            n_tool = sum(1 for e in inc["trace"] if e["kind"] == "tool")
            n_back = sum(1 for e in inc["trace"] if e["kind"] == "backtrack")
            with st.expander(f"🧠 ③ Xem từng bước hội chẩn ({n_tool} lần AI tự gọi công cụ, {n_back} lần quay lui)",
                             expanded=inc["status"] == "diag"):
                for ev in inc["trace"]:
                    render_event(ev)
            if fin and not busy and st.button("🔁 Chẩn đoán lại", key=f"ag_rediag_{sid}"):
                hub.diagnose_async(inc, llms)
                st.rerun()

        # (5)(6) chat
        if fin:
            st.markdown("**⑤ ⑥ Hỏi thêm D-Bot** (AI có thể tự gọi thêm công cụ để trả lời)")
            for msg in inc["chat"]:
                with st.chat_message("DBA" if msg["role"] == "DBA" else "Chief DBA", avatar="🧑‍💻" if msg["role"] == "DBA" else "👨‍⚕️"):
                    st.markdown(msg["text"])
                    tools_used = [e for e in msg.get("steps", []) if e["kind"] == "tool"]
                    if tools_used:
                        with st.expander(f"🛠️ AI đã gọi thêm {len(tools_used)} công cụ"):
                            for e in tools_used:
                                st.markdown(f"`{e['tool']}` `{json.dumps(e['args'], ensure_ascii=False)}`")
                                st.code(e["text"], language="text")
                    if msg.get("new_fix"):
                        st.info(f"🔁 Đã cập nhật lệnh đề xuất: `{msg['new_fix']}`")
            with st.form(f"ag_chat_{sid}", clear_on_submit=True):
                q = st.text_input("Câu hỏi cho D-Bot", placeholder="VD: Bảng này đang có index nào? Vì sao không phải do khóa?")
                if st.form_submit_button("💬 Gửi", disabled=busy) and q.strip():
                    hub.ask_async(inc, q.strip(), llms)
                    st.rerun()

        # (7)(8) duyet
        if fin and inc["status"] in ("wait", "open", "rejected", "error"):
            st.markdown("**⑦ ⑧ DBA duyệt lệnh sửa**")
            fix = st.text_area("Lệnh D-Bot đề xuất (DBA có thể sửa trước khi duyệt)", fin.get("fix_sql", ""), key=f"ag_fix_{sid}_{len(inc['chat'])}", height=100)
            st.caption("Chỉ cho chạy: CREATE INDEX, ANALYZE TABLE, KILL <pid>, hoặc 1 câu SELECT viết lại (app so kết quả với câu gốc).")
            a1, a2 = st.columns(2)
            if a1.button("✅ Duyệt & chạy", type="primary", key=f"ag_ok_{sid}"):
                hub.approve_async(inc, fix, llms)
                st.rerun()
            if a2.button("❌ Từ chối", key=f"ag_no_{sid}"):
                hub.reject(inc)
                st.rerun()

        # (9) ket qua + kiem tra lai
        act = inc.get("action")
        if act:
            st.markdown("**⑨ Kết quả xử lý & D-Bot tự kiểm tra lại**")
            if not act.get("ok"):
                st.warning(act["msg"])
            else:
                b, a = act.get("before"), act.get("after")
                if isinstance(b, (int, float)) and isinstance(a, (int, float)):
                    compare_chart(b, a)
                    st.success(f"`{act.get('sql','')}` → {b:.3f}s → {a:.3f}s (nhanh hơn {b/max(a,1e-4):.0f} lần). {act['msg']}")
                else:
                    st.success(f"`{act.get('sql','')}` → {act['msg']}")
                if act.get("kind") == "rewrite" and not act.get("same"):
                    st.error(f"Câu viết lại cho kết quả {act.get('after_result')} ≠ câu gốc {act.get('before_result')}")
            if inc.get("verify_trace"):
                with st.expander(f"🔍 D-Bot tự kiểm tra lại ({sum(1 for e in inc['verify_trace'] if e['kind']=='tool')} lần gọi công cụ)",
                                 expanded=inc["status"] == "verify"):
                    for ev in inc["verify_trace"]:
                        render_event(ev)
            v = inc.get("verify")
            if v:
                (st.success if v.get("resolved") else st.error)(
                    ("✅ **Đã đóng incident.** " if v.get("resolved") else "⚠️ **Sự cố chưa khỏi** – có thể hỏi thêm hoặc chẩn đoán lại. ")
                    + v.get("summary", ""))
        if busy:
            st.caption("⏳ D-Bot đang làm việc – trang tự cập nhật.")

# ---------------- TAB 7 ----------------
with tabs[7]:
    st.subheader("📊 Tổng kết – bảng chấm AI (dùng cho mục 'So sánh với ChatGPT' trong báo cáo)")
    g = ss.res.get("grades", {})
    rows = []
    for kb, name in (("kb1", "MISSING_INDEXES"), ("kb2", "CORRELATED_SUBQUERY"), ("kb3", "LOCK_CONTENTION"), ("tpch", "TPC-H Q17")):
        row = {"Sự cố": name}
        for p in ("A", "B"):
            ok, spec = g.get(f"{kb}_{p}", (None, None))
            row[f"Prompt {p}: đúng nguyên nhân"] = "—" if ok is None else ("✅" if ok else "❌")
            row[f"Prompt {p}: lệnh cụ thể"] = "—" if spec is None else ("✅" if spec else "❌")
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), width="stretch")
    perf = []
    if "kb1_after" in ss.res:
        perf.append({"Kịch bản": "Thiếu index", "Trước (s)": round(ss.res["kb1_before"], 3), "Sau (s)": round(ss.res["kb1_after"], 3)})
    if "kb2_after" in ss.res:
        perf.append({"Kịch bản": "Truy vấn con", "Trước (s)": round(ss.res["kb2_before"][0], 3), "Sau (s)": round(ss.res["kb2_after"][0], 3)})
    if "tp_slow" in ss.res and "tp_fast" in ss.res:
        perf.append({"Kịch bản": "TPC-H Q17 (viết lại)", "Trước (s)": round(ss.res["tp_slow"][0], 3), "Sau (s)": round(ss.res["tp_fast"][0], 3)})
    if "tp_slow" in ss.res and "tp_idx" in ss.res:
        perf.append({"Kịch bản": "TPC-H Q17 (index)", "Trước (s)": round(ss.res["tp_slow"][0], 3), "Sau (s)": round(ss.res["tp_idx"][0], 3)})
    if perf:
        st.dataframe(pd.DataFrame(perf), width="stretch")
    if ss.answers:
        export = "\n\n".join(f"===== {k} =====\n{v}" for k, v in ss.answers.items() if v)
        st.download_button("💾 Tải toàn bộ câu trả lời AI (.txt)", export, file_name="ai_answers.txt")
    st.caption("Mỗi prompt nên chạy 3 lần (bấm lại nút Hỏi AI) và ghi cả 3 kết quả vào báo cáo.")
