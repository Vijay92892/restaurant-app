-- One-time database bootstrap for a new environment. Run as a PostgreSQL admin:
--
--   psql "$ADMIN_URL" -v owner_password="..." -v app_password="..." -f scripts/bootstrap_db.sql
--
-- Afterwards:
--   MIGRATION_DATABASE_URL  → restaurant_owner  (deploy pipeline runs `alembic upgrade head`)
--   DATABASE_URL            → restaurant_app    (the API; APP_DB_ROLE=restaurant_app)
--
-- Security model: the API role must NOT be a superuser, must NOT have BYPASSRLS and must NOT own
-- the tables, otherwise Row Level Security does not apply to it. The API refuses to start in
-- production if any of these is violated.

\set ON_ERROR_STOP on

CREATE ROLE restaurant_owner LOGIN PASSWORD :'owner_password';
CREATE ROLE restaurant_app LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT;

CREATE DATABASE restaurant OWNER restaurant_owner;
REVOKE ALL ON DATABASE restaurant FROM PUBLIC;
GRANT CONNECT ON DATABASE restaurant TO restaurant_owner, restaurant_app;

\connect restaurant
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO restaurant_owner;
-- Table, function and schema-usage grants for restaurant_app are applied by the migrations.
