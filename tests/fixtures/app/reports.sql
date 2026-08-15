CREATE TABLE monthly_report AS
SELECT t.order_id, s.score
FROM orders t
JOIN order_summary s ON t.order_id = s.order_id;

INSERT INTO audit_log
SELECT * FROM monthly_report;
