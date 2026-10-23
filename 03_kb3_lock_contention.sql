-- =====================================================================
-- KICH BAN 3 - LOCK_CONTENTION  (Table 1 cua paper 5.2, nhom "Concurrent Transaction")
-- Lien he mon hoc: Ch.21 slide 13-15 / sach tr. 789 (2PL, cho khoa, deadlock)
-- Mo 3 cua so (3 tab Query trong MySQL Workbench), chay theo thu tu:
-- =====================================================================

-- ===== CUA SO A (giao tac giu khoa, KHONG commit) =====
USE demo_dbot;
START TRANSACTION;
UPDATE emp SET salary = salary + 1 WHERE id = 1;
-- dung lai o day, chua COMMIT

-- ===== CUA SO B (giao tac bi chan - se "treo") =====
USE demo_dbot;
UPDATE emp SET salary = salary + 2 WHERE id = 1;
-- treo toi da 50 giay (innodb_lock_wait_timeout) roi bao loi 1205

-- ===== CUA SO C ("cong cu" dua cho AI kieu D-Bot) =====
SELECT wait_age, locked_table, locked_type, waiting_query,
       blocking_pid, blocking_query, sql_kill_blocking_connection
FROM sys.innodb_lock_waits\G
-- Trong Workbench bo "\G" o cuoi, thay bang ";"

-- ===== SUA: quay lai CUA SO A =====
ROLLBACK;   -- hoac COMMIT; -> cua so B chay xong ngay
