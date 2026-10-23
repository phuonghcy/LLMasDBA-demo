-- =====================================================================
-- KICH BAN 2 - CORRELATED_SUBQUERY  (Table 1 cua paper 5.2)
-- Lien he mon hoc: Ch.19 slide 17 / sach tr. 704 (Unnesting / decorrelation)
-- =====================================================================
USE demo_dbot;
SET profiling = 1;

-- (1) TAO SU CO: nhan vien co luong cao hon trung binh phong minh
--     Truy van con chay lai cho TUNG dong cua bang ngoai -> rat cham
SELECT COUNT(*) FROM emp_small e
WHERE e.salary > (SELECT AVG(e2.salary) FROM emp_small e2 WHERE e2.dno = e.dno);

-- (2) CONG CU cho AI: EXPLAIN thay "DEPENDENT SUBQUERY"
EXPLAIN SELECT COUNT(*) FROM emp_small e
WHERE e.salary > (SELECT AVG(e2.salary) FROM emp_small e2 WHERE e2.dno = e.dno);

-- (3) SUA: viet lai (unnesting) - tinh AVG moi phong 1 lan roi JOIN
SELECT COUNT(*) FROM emp_small e
JOIN (SELECT dno, AVG(salary) AS a FROM emp_small GROUP BY dno) d ON e.dno = d.dno
WHERE e.salary > d.a;

EXPLAIN SELECT COUNT(*) FROM emp_small e
JOIN (SELECT dno, AVG(salary) AS a FROM emp_small GROUP BY dno) d ON e.dno = d.dno
WHERE e.salary > d.a;

SHOW PROFILES;   -- ket qua COUNT(*) giong nhau, thoi gian khac nhau rat xa
