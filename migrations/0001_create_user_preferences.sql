-- PROPOSED migration — NOT executed by Claude. Review before running.
--
-- This is a pure ADDITION: it creates one new table and touches nothing
-- else. No existing table (events, user, calendar_events, favorites,
-- ratings, reviews, search) is altered, and no existing row anywhere is
-- modified or deleted.
--
-- Note: app.py's startup db.metadata.create_all(...) call deliberately
-- EXCLUDES this table -- simply starting the app will NOT create it.
-- This table is only ever created through an explicit step: either run
-- this file by hand, or run apply_pending_migrations.py (which runs the
-- equivalent CREATE TABLE IF NOT EXISTS through SQLAlchemy). Either way
-- is idempotent and safe to repeat.
--
-- Recommended before running either way: a full `mysqldump afaq_db` backup.

CREATE TABLE IF NOT EXISTS `user_preferences` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `category` VARCHAR(100) NOT NULL,
  `source` VARCHAR(30) NOT NULL DEFAULT 'chatbot_explicit',
  `created_at` DATETIME DEFAULT NULL,
  `updated_at` DATETIME DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_preference_user_category` (`user_id`, `category`),
  CONSTRAINT `fk_user_preferences_user`
    FOREIGN KEY (`user_id`) REFERENCES `user` (`user_id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
