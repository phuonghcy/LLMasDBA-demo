-- CA 3 – TRANH CHẤP KHÓA (chạy từ Workbench, web tự phát hiện)
-- Trước đó: web tab "D-Bot tự chủ" -> 🗑️ Xóa toàn bộ incident -> ▶️ Bật giám sát
-- Cần 2 tab Query trong Workbench. Chạy TỪNG khối theo thứ tự.

-- ===== TAB A: giữ khóa, KHÔNG commit (bôi đen 3 dòng rồi bấm ⚡) =====
USE demo_dbot;
START TRANSACTION;
UPDATE emp SET salary = salary + 1 WHERE id = 1;

-- ===== TAB B: bị chặn -> "treo" (bôi đen 3 dòng rồi bấm ⚡) =====
USE demo_dbot;
SET SESSION innodb_lock_wait_timeout = 300;   -- cho treo lâu, đủ thời gian demo
UPDATE emp SET salary = salary + 2 WHERE id = 1;
-- Sau ~5 giây web báo incident P1 "tranh chấp khóa".
-- D-Bot sẽ đề xuất KILL phiên đang giữ khóa (tab A) -> bấm ✅ Duyệt trên web.

-- ===== Nếu muốn tự gỡ thay vì duyệt KILL: quay lại TAB A chạy =====
-- ROLLBACK;
