CREATE TABLE monthly_report AS
SELECT t.trial_id, s.score
FROM simulated_trials t
JOIN trial_summary s ON t.trial_id = s.trial_id;

INSERT INTO audit_log
SELECT * FROM monthly_report;
