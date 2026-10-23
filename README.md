# LLM As DBA – Demo (Nhóm 3, Hệ CSDL tiên tiến)

Demo bài báo **"LLM As DBA"** (Zhou et al., 2023 – D-Bot), so sánh với **λ-Tune**, chạy trên **MySQL 8** (database `demo_dbot` và **TPC-H**).

- `app/` – web Streamlit: 3 sự cố (thiếu index, truy vấn con tương quan, tranh chấp khóa), mini λ-Tune, TPC-H Q17, **D-Bot tự chủ** (giám sát → incident → 3 agent chẩn đoán → DBA duyệt → tự kiểm tra lại).
- `0x_*.sql` – script chạy tay trong MySQL Workbench cho từng ca.

## Chạy nhanh (Windows)
1. `app\1_cai_dat.bat` – cài thư viện
2. `app\3_tao_TPCH.bat` – tạo dữ liệu TPC-H (tùy chọn)
3. `app\2_chay_demo.bat` – mở http://localhost:8501

Hướng dẫn chi tiết: [`app/HUONG_DAN.md`](app/HUONG_DAN.md) · [`DOC_TRUOC.txt`](DOC_TRUOC.txt)

> API key chỉ nhập trong phiên web, không lưu ra file.
