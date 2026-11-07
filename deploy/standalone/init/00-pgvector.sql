-- Stand-alone Postgres init: the pgvector extension and nothing else.
--
-- Deliberately NOT umrs/docker/init_pgvector.sql, which also creates a LOGIN
-- CREATEDB role 'ejeannot' with password 'ejeannot' — a fixed credential that
-- has no place in a hardened profile, and that the dev overlay would expose on
-- 127.0.0.1:5432. Roles here are created by umrs at boot from the generated
-- POSTGRES_* secrets (server/sql.py create_umrs_server_db_user).
CREATE EXTENSION IF NOT EXISTS vector;
