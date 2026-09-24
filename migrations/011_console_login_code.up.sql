-- Single-use codes that turn an Entra access token, obtained through the Azure CLI, into a
-- browser session, for a tenant where the web sign-in has no consent yet: the Azure CLI is
-- pre-authorized on Turnstile's API, so its token needs none. A code is stored hashed,
-- lives a minute and is deleted as it is redeemed.
CREATE TABLE IF NOT EXISTS console_login_code (
    code_sha256 text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT console_login_code_expiry CHECK (expires_at > created_at)
);