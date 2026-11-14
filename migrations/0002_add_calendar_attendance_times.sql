-- ============================================================
-- PROPOSED MIGRATION -- NOT EXECUTED. Requires explicit approval.
-- ============================================================
--
-- Final Sprint Priority 1.6: persist the EXACT attendance slot the
-- user approved from a Smart Schedule suggestion (e.g. "18:00-20:00"
-- inside a 16:00-23:00 operating window), not just the date.
--
-- REVISED: a single "is this estimated" boolean cannot represent a
-- MIXED slot -- an official, unmovable start time with an
-- AFAQ-estimated end/duration (e.g. a football match with a verified
-- 20:00 kickoff but no listed official end time, where AFAQ's own
-- 120-minute estimate produces 20:00-22:00: the 20:00 is official,
-- the 22:00 is not). Two independent nullable flags, one per
-- boundary, replace the original single `is_time_estimated` column
-- and cover all three real cases plus "no time saved":
--
--   start_time_is_estimated | end_time_is_estimated | meaning
--   ------------------------+------------------------+------------------
--   FALSE                   | FALSE                  | fully official
--   TRUE                    | TRUE                   | fully estimated
--   FALSE                   | TRUE                   | mixed: official
--                           |                        | start, estimated end
--   NULL                    | NULL                   | no time saved
--                           |                        | (date-only entry)
--
-- This is still the smallest safe extension to `calendar_events`:
--   - All four new columns are NULLable, so every existing row
--     (date-only, no time) remains perfectly valid with no backfill.
--   - No column is renamed or dropped; `scheduled_date` keeps its
--     exact current meaning and behavior.
--
-- Like migrations/0001_create_user_preferences.sql, this file is
-- NOT applied automatically. app.py's startup create_all() does not
-- touch existing tables' columns at all (CREATE TABLE IF NOT EXISTS
-- semantics only -- it never runs ALTER TABLE), so simply starting
-- the app cannot apply this change either. Run this SQL yourself
-- (or wait for an approved, idempotent migration helper mirroring
-- apply_pending_migrations.py) only once you have approved it.
--
-- NOTE: `ADD COLUMN IF NOT EXISTS` requires MySQL 8.0.29+. If your
-- server is older, drop the "IF NOT EXISTS" clauses below (the
-- migration is only ever run once anyway).

ALTER TABLE `calendar_events`
  ADD COLUMN IF NOT EXISTS `attendance_start_time` TIME NULL
    COMMENT 'Approved visit start time, e.g. 20:00. NULL for pre-existing date-only entries or entries saved without a confirmed time.',
  ADD COLUMN IF NOT EXISTS `attendance_end_time` TIME NULL
    COMMENT 'Approved visit end time, e.g. 22:00. NULL under the same conditions as attendance_start_time.',
  ADD COLUMN IF NOT EXISTS `start_time_is_estimated` TINYINT(1) NULL
    COMMENT 'NULL = no time saved. 0 = attendance_start_time is the event''s verified official start. 1 = AFAQ selected/estimated it.',
  ADD COLUMN IF NOT EXISTS `end_time_is_estimated` TINYINT(1) NULL
    COMMENT 'NULL = no time saved. 0 = attendance_end_time is the event''s verified official end. 1 = AFAQ selected/estimated it (e.g. a duration estimate applied to an official start).';

-- Nothing below this line writes any data -- schema only.
