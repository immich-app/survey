-- The OIDC sub of the user who created the survey, so creators can delete
-- their own surveys without being admins. Nullable: surveys restored from a
-- backup taken before this column existed have no recorded creator.
ALTER TABLE surveys ADD COLUMN created_by TEXT;
