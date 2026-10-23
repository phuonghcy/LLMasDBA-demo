-- =====================================================================
-- KICH BAN 1 - MISSING_INDEXES  (Table 1 cua paper 5.2, nhom "Slow Query")
-- Lien he mon hoc: Ch.17 slide 34 / sach tr. 640 (Tuning Indexes)
-- =====================================================================
USE demo_dbot;
DROP PROCEDURE IF EXISTS run_kb1;
DROP PROCEDURE IF EXISTS drop_idx_dno;
DELIMITER //
CREATE PROCEDURE drop_idx_dno()
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.statistics
             WHERE table_schema='demo_dbot' AND table_name='emp' AND index_name='idx_dno') THEN
    DROP INDEX idx_dno ON emp;
  END IF;
END //
-- "Workload": n truy van loc theo phong ban, mo phong ung dung goi lien tuc
CREATE PROCEDURE run_kb1(IN n INT)
BEGIN
  DECLARE t0 DATETIME(6) DEFAULT NOW(6);
  DECLARE i INT DEFAULT 1;
  DECLARE c INT; DECLARE a DECIMAL(12,2);
  WHILE i <= n DO
    SELECT COUNT(*), AVG(salary) INTO c, a FROM emp WHERE dno = i;
    SET i = i + 1;
  END WHILE;
  SELECT n AS so_truy_van, ROUND(TIMESTAMPDIFF(MICROSECOND, t0, NOW(6))/1e6, 3) AS tong_giay;
END //
DELIMITER ;
CALL drop_idx_dno();
CALL sys.ps_truncate_all_tables(FALSE);   -- xoa thong ke cu de "cong cu" chi thay demo

-- (1) TAO SU CO: chay workload 30 truy van, xem tong thoi gian
CALL run_kb1(30);

-- (2) "CONG CU" dua cho AI kieu D-Bot
SELECT COUNT(*), AVG(salary) FROM emp WHERE dno = 42;
EXPLAIN SELECT COUNT(*), AVG(salary) FROM emp WHERE dno = 42;
SELECT query, exec_count, total_latency, rows_examined_avg
FROM sys.statements_with_full_table_scans
WHERE db = 'demo_dbot' AND query LIKE 'SELECT%' LIMIT 3;

-- (3) SUA: tao index (lenh AI kieu D-Bot nen de xuat), chay lai workload
CREATE INDEX idx_dno ON emp(dno);
CALL run_kb1(30);
EXPLAIN SELECT COUNT(*), AVG(salary) FROM emp WHERE dno = 42;   -- type = ref, rows ~ 10.000
