-- =====================================================================
-- MINI lambda-Tune (paper 5.5): AI de xuat cau hinh -> CHAY THAT -> chon bo nhanh nhat
-- Lien he mon hoc: Ch.17 s38-40 (thiet ke vat ly), Ch.16 s17 (buffer),
--                  Ch.19 s43 (EXPLAIN), vong do-chon ~ "Adaptive optimization" Ch.19 s46
-- Cach dung: thay C1-C3 bang cau hinh ChatGPT de xuat (prompt trong file prompts).
-- Moi truy van co hint MAX_EXECUTION_TIME(10000) = timeout 10 giay (giong lambda-Tune):
--   cau hinh qua te se bi MySQL ngat (loi 3024) thay vi chay mai.
-- =====================================================================
USE demo_dbot;
DROP PROCEDURE IF EXISTS reset_config;
DELIMITER //
CREATE PROCEDURE reset_config()
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema='demo_dbot' AND table_name='emp' AND index_name='idx_dno') THEN
    DROP INDEX idx_dno ON emp; END IF;
  IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema='demo_dbot' AND table_name='emp' AND index_name='idx_salary') THEN
    DROP INDEX idx_salary ON emp; END IF;
  IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema='demo_dbot' AND table_name='emp' AND index_name='idx_dno_salary') THEN
    DROP INDEX idx_dno_salary ON emp; END IF;
  SET GLOBAL innodb_buffer_pool_size = 134217728;   -- 128MB mac dinh
END //
DELIMITER ;

-- C0: cau hinh mac dinh (moc so sanh)
CALL reset_config();
SET @t0 = NOW(6);
-- Q1: thong ke 20 phong ban
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r1 FROM
  (SELECT dno, COUNT(*), AVG(salary) FROM emp WHERE dno BETWEEN 1 AND 20 GROUP BY dno) q1;
-- Q2: JOIN emp - dept (join key  emp.dno : dept.dno)
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r2 FROM
  (SELECT d.dname, AVG(e.salary) FROM emp e JOIN dept d ON e.dno = d.dno
   WHERE d.budget > 800000 GROUP BY d.dname) q2;
-- Q3: loc theo khoang luong roi nhom
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r3 FROM
  (SELECT dno, COUNT(*) FROM emp WHERE salary BETWEEN 50000 AND 50500 GROUP BY dno) q3;
SELECT 'C0_default' AS config, ROUND(TIMESTAMPDIFF(MICROSECOND, @t0, NOW(6))/1e6, 3) AS workload_seconds;

-- C1 (vi du AI de xuat): chi tang buffer pool 512MB, khong tao index
CALL reset_config();
SET GLOBAL innodb_buffer_pool_size = 536870912;
SET @t0 = NOW(6);
-- Q1: thong ke 20 phong ban
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r1 FROM
  (SELECT dno, COUNT(*), AVG(salary) FROM emp WHERE dno BETWEEN 1 AND 20 GROUP BY dno) q1;
-- Q2: JOIN emp - dept (join key  emp.dno : dept.dno)
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r2 FROM
  (SELECT d.dname, AVG(e.salary) FROM emp e JOIN dept d ON e.dno = d.dno
   WHERE d.budget > 800000 GROUP BY d.dname) q2;
-- Q3: loc theo khoang luong roi nhom
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r3 FROM
  (SELECT dno, COUNT(*) FROM emp WHERE salary BETWEEN 50000 AND 50500 GROUP BY dno) q3;
SELECT 'C1_buffer_only' AS config, ROUND(TIMESTAMPDIFF(MICROSECOND, @t0, NOW(6))/1e6, 3) AS workload_seconds;

-- C2 (vi du AI de xuat): index tren khoa JOIN emp.dno
CALL reset_config();
CREATE INDEX idx_dno ON emp(dno);
SET @t0 = NOW(6);
-- Q1: thong ke 20 phong ban
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r1 FROM
  (SELECT dno, COUNT(*), AVG(salary) FROM emp WHERE dno BETWEEN 1 AND 20 GROUP BY dno) q1;
-- Q2: JOIN emp - dept (join key  emp.dno : dept.dno)
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r2 FROM
  (SELECT d.dname, AVG(e.salary) FROM emp e JOIN dept d ON e.dno = d.dno
   WHERE d.budget > 800000 GROUP BY d.dname) q2;
-- Q3: loc theo khoang luong roi nhom
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r3 FROM
  (SELECT dno, COUNT(*) FROM emp WHERE salary BETWEEN 50000 AND 50500 GROUP BY dno) q3;
SELECT 'C2_idx_dno' AS config, ROUND(TIMESTAMPDIFF(MICROSECOND, @t0, NOW(6))/1e6, 3) AS workload_seconds;

-- C3 (vi du AI de xuat): index phu (dno, salary) + index salary + buffer 512MB
CALL reset_config();
SET GLOBAL innodb_buffer_pool_size = 536870912;
CREATE INDEX idx_dno_salary ON emp(dno, salary);
CREATE INDEX idx_salary ON emp(salary);
SET @t0 = NOW(6);
-- Q1: thong ke 20 phong ban
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r1 FROM
  (SELECT dno, COUNT(*), AVG(salary) FROM emp WHERE dno BETWEEN 1 AND 20 GROUP BY dno) q1;
-- Q2: JOIN emp - dept (join key  emp.dno : dept.dno)
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r2 FROM
  (SELECT d.dname, AVG(e.salary) FROM emp e JOIN dept d ON e.dno = d.dno
   WHERE d.budget > 800000 GROUP BY d.dname) q2;
-- Q3: loc theo khoang luong roi nhom
SELECT /*+ MAX_EXECUTION_TIME(10000) */ COUNT(*) INTO @r3 FROM
  (SELECT dno, COUNT(*) FROM emp WHERE salary BETWEEN 50000 AND 50500 GROUP BY dno) q3;
SELECT 'C3_covering_idx' AS config, ROUND(TIMESTAMPDIFF(MICROSECOND, @t0, NOW(6))/1e6, 3) AS workload_seconds;

-- Chon cau hinh co workload_seconds nho nhat; sau do tra ve mac dinh
CALL reset_config();
