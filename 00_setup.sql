-- =====================================================================
-- DEMO "LLM As DBA" - Buoc 0: tao du lieu (MySQL 8.0)
-- Chay 1 lan truoc buoi bao cao (~10-60 giay tuy may)
-- =====================================================================
DROP DATABASE IF EXISTS demo_dbot;
CREATE DATABASE demo_dbot;
USE demo_dbot;

-- Bang nhan vien 1 trieu dong (giong EMPLOYEE cua COMPANY nhung lon hon)
CREATE TABLE emp (
  id     INT AUTO_INCREMENT PRIMARY KEY,
  dno    INT,            -- ma phong ban 1..100
  salary INT,            -- luong 20.000..100.000
  name   VARCHAR(30)
);

-- Bang phong ban 100 dong (dung cho phep JOIN o phan mini lambda-Tune)
CREATE TABLE dept (
  dno    INT PRIMARY KEY,
  dname  VARCHAR(30),
  budget INT
);

SET SESSION cte_max_recursion_depth = 1000000;

INSERT INTO emp (dno, salary, name)
  WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 1000000)
  SELECT FLOOR(1 + RAND()*100), FLOOR(20000 + RAND()*80000), CONCAT('E', n) FROM s;

INSERT INTO dept (dno, dname, budget)
  WITH RECURSIVE s(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM s WHERE n < 100)
  SELECT n, CONCAT('Dept_', n), FLOOR(100000 + RAND()*900000) FROM s;

-- Bang nho 5.000 dong cho kich ban truy van con tuong quan
-- (neu may cham, giam xuong 3000; neu may nhanh, tang len 8000)
CREATE TABLE emp_small AS SELECT * FROM emp WHERE id <= 5000;

ANALYZE TABLE emp, dept, emp_small;
SELECT (SELECT COUNT(*) FROM emp) AS emp_rows,
       (SELECT COUNT(*) FROM dept) AS dept_rows,
       (SELECT COUNT(*) FROM emp_small) AS emp_small_rows;
