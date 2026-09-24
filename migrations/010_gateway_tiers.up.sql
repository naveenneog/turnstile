-- Tiers for a Claude gateway that takes its governance from Turnstile. Each names the
-- Microsoft Entra group whose members hold the tier and the limits the gateway enforces
-- for them. Turnstile stores and returns them as written; the gateway's reconciler is what
-- applies them, so nothing here is enforced by Turnstile itself.
--
-- Writes replace the whole set in one transaction, as the organization catalog does, so a
-- reader never sees half of an update.
CREATE TABLE IF NOT EXISTS gateway_tier (
    tier_id text PRIMARY KEY CHECK (tier_id ~ '^[a-z][a-z0-9-]{0,39}$'),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    entra_group text NOT NULL CHECK (length(entra_group) BETWEEN 1 AND 256),
    tokens_per_minute integer NOT NULL CHECK (tokens_per_minute > 0),
    tokens_per_day bigint NOT NULL CHECK (tokens_per_day > 0),
    models jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(models) = 'array'),
    position integer NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text NOT NULL
);
