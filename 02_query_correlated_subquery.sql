-- CA 2 – TRUY VẤN CON TƯƠNG QUAN (chạy từ Workbench, web tự phát hiện)
-- Trước đó: web tab "D-Bot tự chủ" -> 🗑️ Xóa toàn bộ incident -> ▶️ Bật giám sát
-- Bấm ⚡ (Execute all). Câu dưới chạy khoảng 5–10 giây -> web báo incident.
USE demo_dbot;
SELECT COUNT(*) FROM emp_small e
WHERE e.salary > (SELECT AVG(e2.salary) FROM emp_small e2 WHERE e2.dno = e.dno);
