-- Optional layer 3: PostgreSQL Row-Level Security.
-- Apply once, after the tables exist, as the table owner / migration role.
--
-- IMPORTANT: superusers and roles with BYPASSRLS ignore RLS entirely. The application must
-- connect as an ordinary role, e.g.:
--   CREATE ROLE billing_app LOGIN PASSWORD '...';
--   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO billing_app;
--
-- The app sets `app.current_tenant` per transaction (see app/tenancy.py). If it is unset,
-- NULLIF(...) yields NULL, the predicate is never true, and the query sees zero rows (fail closed).
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['users', 'subscriptions'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            $p$CREATE POLICY tenant_isolation ON %I
               USING      (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
               WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)$p$,
            t);
    END LOOP;
END
$$;
