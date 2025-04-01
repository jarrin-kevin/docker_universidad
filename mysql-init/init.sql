CREATE DATABASE IF NOT EXISTS universidad;
-- Use the universidad database
USE universidad;

-- Grant privileges
GRANT ALL PRIVILEGES ON universidad.* TO 'root'@'%';
FLUSH PRIVILEGES;
