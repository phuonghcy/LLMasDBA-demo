-- =====================================================================
-- DEMO "LLM As DBA" - TPC-H Q17 (chay tay trong MySQL Workbench)
-- Can tao database tpch truoc (app tab 5 hoac 3_tao_TPCH.bat)
-- =====================================================================
USE tpch;

-- 1) Q17 goc: truy van con tuong quan -> EXPLAIN co DEPENDENT SUBQUERY (~10 giay voi SF 0.05)
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

-- 2) Sua cach 1 - unnesting (Ch.19 slide 17): ~0,4 giay, cung ket qua
SELECT SUM(l.l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem l
JOIN part p ON p.p_partkey = l.l_partkey
JOIN (SELECT l_partkey, 0.2 * AVG(l_quantity) AS lim
      FROM lineitem GROUP BY l_partkey) a ON a.l_partkey = l.l_partkey
WHERE p.p_brand = 'Brand#23' AND p.p_container = 'MED BOX'
  AND l.l_quantity < a.lim;

-- 3) Sua cach 2 - index tren khoa JOIN: ~0,013 giay
CREATE INDEX idx_l_partkey ON lineitem(l_partkey);
SELECT SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem, part
WHERE p_partkey = l_partkey
  AND p_brand = 'Brand#23' AND p_container = 'MED BOX'
  AND l_quantity < (SELECT 0.2 * AVG(l_quantity) FROM lineitem
                    WHERE l_partkey = p_partkey);

-- Tra lai trang thai ban dau
DROP INDEX idx_l_partkey ON lineitem;
