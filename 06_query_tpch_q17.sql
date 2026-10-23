-- CA 4 – TPC-H Q17 (chạy từ Workbench, web tự phát hiện)
-- Trước đó: web tab "D-Bot tự chủ" -> 🗑️ Xóa toàn bộ incident -> ▶️ Bật giám sát
-- Bấm ⚡ (Execute all). Câu Q17 chạy khoảng 10 giây -> web báo incident.
USE tpch;
-- Xóa index nếu lần demo trước đã tạo (không có thì bỏ qua, không báo lỗi)
SET @s = IF(EXISTS(SELECT 1 FROM information_schema.statistics
                   WHERE table_schema='tpch' AND table_name='lineitem' AND index_name='idx_l_partkey'),
            'DROP INDEX idx_l_partkey ON lineitem', 'DO 0');
PREPARE x FROM @s; EXECUTE x; DEALLOCATE PREPARE x;
SELECT SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem, part
WHERE p_partkey = l_partkey
  AND p_brand = 'Brand#23' AND p_container = 'MED BOX'
  AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem
                    WHERE l_partkey = p_partkey);


-- =====================================================================
-- PHẦN 2 – BẢN KHÔNG LỖI (chạy SAU khi D-Bot đã chẩn đoán)
-- KHÔNG dùng ⚡ Execute all cho phần này: bôi đen từng khối rồi bấm
-- Ctrl+Shift+Enter (Execute selection).
-- =====================================================================

-- 2A) Viết lại bằng unnesting (không cần index): ~0,4 giây, cùng kết quả
SELECT SUM(l.l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem l
JOIN part p ON p.p_partkey = l.l_partkey
JOIN (SELECT l_partkey, 0.2 * AVG(l_quantity) AS lim
      FROM lineitem GROUP BY l_partkey) a ON a.l_partkey = l.l_partkey
WHERE p.p_brand = 'Brand#23' AND p.p_container = 'MED BOX'
  AND l.l_quantity < a.lim;

-- 2B) Sau khi bấm ✅ Duyệt & chạy trên web (D-Bot đã tạo idx_l_partkey):
--     chạy lại Q17 gốc -> ~0,013 giây, web không báo incident nữa.
--     Nếu D-Bot chưa tạo index thì khối dưới tự tạo (đã có thì bỏ qua, không lỗi).
SET @s = IF(EXISTS(SELECT 1 FROM information_schema.statistics
                   WHERE table_schema='tpch' AND table_name='lineitem' AND index_name='idx_l_partkey'),
            'DO 0', 'CREATE INDEX idx_l_partkey ON lineitem(l_partkey)');
PREPARE x FROM @s; EXECUTE x; DEALLOCATE PREPARE x;

EXPLAIN
SELECT SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem, part
WHERE p_partkey = l_partkey
  AND p_brand = 'Brand#23' AND p_container = 'MED BOX'
  AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem
                    WHERE l_partkey = p_partkey);

SELECT SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem, part
WHERE p_partkey = l_partkey
  AND p_brand = 'Brand#23' AND p_container = 'MED BOX'
  AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem
                    WHERE l_partkey = p_partkey);
