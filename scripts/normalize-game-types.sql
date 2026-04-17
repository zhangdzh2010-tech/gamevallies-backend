-- One-time data cleanup: map legacy game_type values to casual | puzzle | education.
-- Run against your MySQL database after deploying the unified game type logic.

UPDATE game
SET game_type = CASE
  WHEN LOWER(TRIM(game_type)) IN ('puzzle', 'logic', 'brain') THEN 'puzzle'
  WHEN LOWER(TRIM(game_type)) IN ('education', 'educational', 'edu') THEN 'education'
  WHEN LOWER(TRIM(game_type)) IN ('casual', '休闲') THEN 'casual'
  ELSE 'casual'
END
WHERE game_type IS NOT NULL AND TRIM(game_type) <> '';

UPDATE game SET game_type = 'casual' WHERE game_type IS NULL OR TRIM(game_type) = '';
