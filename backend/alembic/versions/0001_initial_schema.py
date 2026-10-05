"""Initial schema: tenancy, staff auth, tables/PIN/sessions, menu, orders, audit.

Security properties established here (see docs/02-data-model.md, docs/03-security.md):
  * every tenant table has restaurant_id + Row Level Security keyed on app.restaurant_id
  * composite (id, restaurant_id) foreign keys make cross-tenant references impossible
  * the runtime role gets least privilege; audit/event logs are append-only
  * immutable columns (e.g. a table session's table) are protected by triggers
  * SECURITY DEFINER resolver functions return routing ids only, for pre-tenant lookups

Revision ID: 0001
Revises:
Create Date: 2026-10-04
"""

import re
from collections.abc import Sequence

from alembic import context, op
from app.core.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "users",
    "auth_sessions",
    "refresh_tokens",
    "password_reset_tokens",
    "dining_tables",
    "table_qr_tokens",
    "pin_versions",
    "table_sessions",
    "menu_categories",
    "menu_items",
    "menu_modifier_groups",
    "menu_modifiers",
    "menu_item_modifier_groups",
    "orders",
    "order_items",
    "order_item_modifiers",
    "order_events",
    "audit_logs",
)

GRANTS = {
    "SELECT, INSERT, UPDATE": (
        "restaurants",
        "users",
        "dining_tables",
        "table_qr_tokens",
        "pin_versions",
        "menu_categories",
        "menu_items",
        "menu_modifier_groups",
        "menu_modifiers",
        "orders",
    ),
    "SELECT, INSERT, UPDATE, DELETE": (
        "auth_sessions",
        "refresh_tokens",
        "password_reset_tokens",
        "table_sessions",
        "menu_item_modifier_groups",
    ),
    # Order lines are immutable once placed; logs are append-only.
    "SELECT, INSERT": ("order_items", "order_item_modifiers", "order_events", "audit_logs"),
}

UPDATED_AT_TABLES = (
    "restaurants",
    "users",
    "dining_tables",
    "menu_categories",
    "menu_items",
    "menu_modifier_groups",
    "menu_modifiers",
    "orders",
)

IMMUTABLE_COLUMNS = {
    "users": ("restaurant_id", "created_at"),
    "auth_sessions": ("restaurant_id", "user_id", "created_at"),
    "refresh_tokens": ("restaurant_id", "session_id", "token_hash", "parent_id", "created_at"),
    "password_reset_tokens": ("restaurant_id", "user_id", "token_hash", "created_at"),
    "table_qr_tokens": ("restaurant_id", "table_id", "token", "created_at"),
    "pin_versions": (
        "restaurant_id",
        "version",
        "pin_hmac",
        "pin_ciphertext",
        "key_version",
        "reason",
        "created_by_user_id",
        "created_at",
    ),
    "table_sessions": (
        "restaurant_id",
        "table_id",
        "token_hash",
        "pin_version",
        "created_at",
        "expires_at",
    ),
    "orders": (
        "restaurant_id",
        "order_number",
        "table_id",
        "source",
        "table_session_id",
        "placed_by_user_id",
        "idempotency_key",
        "request_fingerprint",
        "created_at",
    ),
}

RESOLVER_FUNCTIONS = (
    "app_resolve_restaurant_code(text)",
    "app_resolve_qr_token(text)",
    "app_resolve_table_session(bytea)",
    "app_resolve_refresh_token(bytea)",
    "app_resolve_password_reset_token(bytea)",
    "app_due_pin_rotations(timestamptz)",
)

SCHEMA = [
    # --- helper functions ------------------------------------------------------------------------
    """
    CREATE FUNCTION app_current_restaurant_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT NULLIF(current_setting('app.restaurant_id', true), '')::uuid $$
    """,
    """
    CREATE FUNCTION app_touch_updated_at() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      NEW.updated_at := now();
      RETURN NEW;
    END $$
    """,
    """
    CREATE FUNCTION app_immutable_columns() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
      col text;
      old_row jsonb := to_jsonb(OLD);
      new_row jsonb := to_jsonb(NEW);
    BEGIN
      FOREACH col IN ARRAY TG_ARGV LOOP
        IF (new_row -> col) IS DISTINCT FROM (old_row -> col) THEN
          RAISE EXCEPTION 'column %.% is immutable', TG_TABLE_NAME, col
            USING ERRCODE = 'check_violation';
        END IF;
      END LOOP;
      RETURN NEW;
    END $$
    """,
    # --- tenancy & staff -------------------------------------------------------------------------
    """
    CREATE TABLE restaurants (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      code text NOT NULL,
      name text NOT NULL,
      timezone text NOT NULL DEFAULT 'Asia/Kolkata',
      status text NOT NULL DEFAULT 'active',
      pin_length smallint NOT NULL DEFAULT 4,
      pin_rotation_minutes integer DEFAULT 240,
      table_session_ttl_minutes integer NOT NULL DEFAULT 180,
      waiter_can_cancel_preparing boolean NOT NULL DEFAULT false,
      kitchen_delay_minutes integer NOT NULL DEFAULT 15,
      order_seq bigint NOT NULL DEFAULT 0,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_restaurants_code UNIQUE (code),
      CONSTRAINT ck_restaurants_code CHECK (code ~ '^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$'),
      CONSTRAINT ck_restaurants_name CHECK (char_length(name) BETWEEN 1 AND 120),
      CONSTRAINT ck_restaurants_timezone CHECK (char_length(timezone) BETWEEN 1 AND 64),
      CONSTRAINT ck_restaurants_status CHECK (status IN ('active', 'suspended')),
      CONSTRAINT ck_restaurants_pin_length CHECK (pin_length IN (4, 6)),
      CONSTRAINT ck_restaurants_pin_rotation
        CHECK (pin_rotation_minutes IS NULL OR pin_rotation_minutes IN (120, 240, 360)),
      CONSTRAINT ck_restaurants_session_ttl CHECK (table_session_ttl_minutes BETWEEN 30 AND 720),
      CONSTRAINT ck_restaurants_delay CHECK (kitchen_delay_minutes BETWEEN 5 AND 120),
      CONSTRAINT ck_restaurants_order_seq CHECK (order_seq >= 0)
    )
    """,
    """
    CREATE TABLE users (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      username text NOT NULL,
      email text,
      display_name text NOT NULL,
      role text NOT NULL,
      password_hash text NOT NULL,
      is_active boolean NOT NULL DEFAULT true,
      must_change_password boolean NOT NULL DEFAULT false,
      password_changed_at timestamptz NOT NULL DEFAULT now(),
      last_login_at timestamptz,
      deleted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_users_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT ck_users_username CHECK (username ~ '^[a-z0-9._-]{3,32}$'),
      CONSTRAINT ck_users_email CHECK (
        email IS NULL OR (char_length(email) BETWEEN 3 AND 254 AND email = lower(email)
                          AND position('@' IN email) > 1)),
      CONSTRAINT ck_users_display_name CHECK (char_length(display_name) BETWEEN 1 AND 80),
      CONSTRAINT ck_users_role CHECK (role IN ('owner', 'admin', 'waiter', 'kitchen')),
      CONSTRAINT ck_users_password_hash CHECK (password_hash LIKE '$argon2id$%')
    )
    """,
    "CREATE UNIQUE INDEX uq_users_username ON users (restaurant_id, username) "
    "WHERE deleted_at IS NULL",
    "CREATE UNIQUE INDEX uq_users_email ON users (restaurant_id, email) "
    "WHERE email IS NOT NULL AND deleted_at IS NULL",
    "CREATE UNIQUE INDEX uq_users_one_owner ON users (restaurant_id) "
    "WHERE role = 'owner' AND deleted_at IS NULL",
    """
    CREATE TABLE auth_sessions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      user_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      last_used_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      revoked_at timestamptz,
      revoke_reason text,
      user_agent text,
      created_ip inet,
      CONSTRAINT uq_auth_sessions_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT fk_auth_sessions_user FOREIGN KEY (user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT ck_auth_sessions_expiry CHECK (expires_at > created_at),
      CONSTRAINT ck_auth_sessions_user_agent
        CHECK (user_agent IS NULL OR char_length(user_agent) <= 200),
      CONSTRAINT ck_auth_sessions_revoke_reason CHECK (revoke_reason IS NULL OR revoke_reason IN (
        'logout', 'logout_all', 'password_changed', 'password_reset', 'refresh_token_reuse',
        'user_deactivated', 'admin_reset', 'user_deleted')),
      CONSTRAINT ck_auth_sessions_revoked CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL))
    )
    """,
    "CREATE INDEX ix_auth_sessions_user_active ON auth_sessions (user_id) WHERE revoked_at IS NULL",
    """
    CREATE TABLE refresh_tokens (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      session_id uuid NOT NULL,
      parent_id uuid,
      token_hash bytea NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      rotated_at timestamptz,
      revoked_at timestamptz,
      CONSTRAINT uq_refresh_tokens_token_hash UNIQUE (token_hash),
      CONSTRAINT fk_refresh_tokens_session FOREIGN KEY (session_id, restaurant_id)
        REFERENCES auth_sessions (id, restaurant_id) ON DELETE CASCADE,
      CONSTRAINT ck_refresh_tokens_hash CHECK (octet_length(token_hash) = 32)
    )
    """,
    "CREATE INDEX ix_refresh_tokens_session ON refresh_tokens (session_id)",
    """
    CREATE TABLE password_reset_tokens (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      user_id uuid NOT NULL,
      token_hash bytea NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      used_at timestamptz,
      CONSTRAINT uq_password_reset_tokens_token_hash UNIQUE (token_hash),
      CONSTRAINT fk_password_reset_tokens_user FOREIGN KEY (user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT ck_password_reset_tokens_hash CHECK (octet_length(token_hash) = 32)
    )
    """,
    "CREATE INDEX ix_password_reset_tokens_user ON password_reset_tokens (user_id)",
    # --- tables, QR, PIN, customer sessions ------------------------------------------------------
    """
    CREATE TABLE dining_tables (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      label text NOT NULL,
      is_active boolean NOT NULL DEFAULT true,
      sort_order integer NOT NULL DEFAULT 0,
      deleted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_dining_tables_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT ck_dining_tables_label CHECK (char_length(label) BETWEEN 1 AND 20),
      CONSTRAINT ck_dining_tables_sort CHECK (sort_order BETWEEN 0 AND 100000)
    )
    """,
    "CREATE UNIQUE INDEX uq_dining_tables_label ON dining_tables (restaurant_id, lower(label)) "
    "WHERE deleted_at IS NULL",
    """
    CREATE TABLE table_qr_tokens (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      table_id uuid NOT NULL,
      token text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz,
      CONSTRAINT uq_table_qr_tokens_token UNIQUE (token),
      CONSTRAINT fk_table_qr_tokens_table FOREIGN KEY (table_id, restaurant_id)
        REFERENCES dining_tables (id, restaurant_id),
      CONSTRAINT ck_table_qr_tokens_token CHECK (token ~ '^[A-Za-z0-9_-]{16,64}$')
    )
    """,
    "CREATE UNIQUE INDEX uq_table_qr_tokens_active ON table_qr_tokens (table_id) "
    "WHERE revoked_at IS NULL",
    """
    CREATE TABLE pin_versions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      version integer NOT NULL,
      pin_hmac bytea NOT NULL,
      pin_ciphertext bytea NOT NULL,
      key_version smallint NOT NULL DEFAULT 1,
      reason text NOT NULL,
      created_by_user_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      superseded_at timestamptz,
      CONSTRAINT uq_pin_versions_restaurant_version UNIQUE (restaurant_id, version),
      CONSTRAINT fk_pin_versions_created_by FOREIGN KEY (created_by_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT ck_pin_versions_version CHECK (version > 0),
      CONSTRAINT ck_pin_versions_hmac CHECK (octet_length(pin_hmac) = 32),
      CONSTRAINT ck_pin_versions_ciphertext CHECK (octet_length(pin_ciphertext) BETWEEN 29 AND 64),
      CONSTRAINT ck_pin_versions_key_version CHECK (key_version > 0),
      CONSTRAINT ck_pin_versions_reason CHECK (reason IN ('initial', 'manual', 'scheduled'))
    )
    """,
    "CREATE UNIQUE INDEX uq_pin_versions_current ON pin_versions (restaurant_id) "
    "WHERE superseded_at IS NULL",
    """
    CREATE TABLE table_sessions (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      table_id uuid NOT NULL,
      token_hash bytea NOT NULL,
      pin_version integer NOT NULL,
      status text NOT NULL DEFAULT 'active',
      created_at timestamptz NOT NULL DEFAULT now(),
      expires_at timestamptz NOT NULL,
      last_seen_at timestamptz NOT NULL DEFAULT now(),
      ended_at timestamptz,
      end_reason text,
      ended_by_user_id uuid,
      CONSTRAINT uq_table_sessions_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT uq_table_sessions_id_table_restaurant UNIQUE (id, table_id, restaurant_id),
      CONSTRAINT uq_table_sessions_token_hash UNIQUE (token_hash),
      CONSTRAINT fk_table_sessions_table FOREIGN KEY (table_id, restaurant_id)
        REFERENCES dining_tables (id, restaurant_id),
      CONSTRAINT fk_table_sessions_ended_by FOREIGN KEY (ended_by_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT fk_table_sessions_pin_version FOREIGN KEY (restaurant_id, pin_version)
        REFERENCES pin_versions (restaurant_id, version),
      CONSTRAINT ck_table_sessions_hash CHECK (octet_length(token_hash) = 32),
      CONSTRAINT ck_table_sessions_status CHECK (status IN ('active', 'revoked', 'closed')),
      CONSTRAINT ck_table_sessions_expiry CHECK (expires_at > created_at),
      CONSTRAINT ck_table_sessions_ended CHECK ((status = 'active') = (ended_at IS NULL)),
      CONSTRAINT ck_table_sessions_end_reason CHECK (end_reason IS NULL OR end_reason IN (
        'invalidated_by_staff', 'pin_rotated', 'table_closed', 'customer_left', 'table_disabled'))
    )
    """,
    "CREATE INDEX ix_table_sessions_active ON table_sessions (restaurant_id, table_id) "
    "WHERE status = 'active'",
    # --- menu ------------------------------------------------------------------------------------
    """
    CREATE TABLE menu_categories (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      name text NOT NULL,
      description text,
      sort_order integer NOT NULL DEFAULT 0,
      is_active boolean NOT NULL DEFAULT true,
      deleted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_menu_categories_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT ck_menu_categories_name CHECK (char_length(name) BETWEEN 1 AND 60),
      CONSTRAINT ck_menu_categories_description
        CHECK (description IS NULL OR char_length(description) <= 300),
      CONSTRAINT ck_menu_categories_sort CHECK (sort_order BETWEEN 0 AND 100000)
    )
    """,
    "CREATE UNIQUE INDEX uq_menu_categories_name ON menu_categories (restaurant_id, lower(name)) "
    "WHERE deleted_at IS NULL",
    """
    CREATE TABLE menu_items (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      category_id uuid NOT NULL,
      name text NOT NULL,
      description text,
      dietary text,
      display_price_paise integer,
      image_path text,
      is_available boolean NOT NULL DEFAULT true,
      is_active boolean NOT NULL DEFAULT true,
      sort_order integer NOT NULL DEFAULT 0,
      deleted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_menu_items_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT fk_menu_items_category FOREIGN KEY (category_id, restaurant_id)
        REFERENCES menu_categories (id, restaurant_id),
      CONSTRAINT ck_menu_items_name CHECK (char_length(name) BETWEEN 1 AND 80),
      CONSTRAINT ck_menu_items_description
        CHECK (description IS NULL OR char_length(description) <= 500),
      CONSTRAINT ck_menu_items_dietary
        CHECK (dietary IS NULL OR dietary IN ('veg', 'non_veg', 'egg')),
      CONSTRAINT ck_menu_items_price
        CHECK (display_price_paise IS NULL OR display_price_paise BETWEEN 0 AND 100000000),
      CONSTRAINT ck_menu_items_image_path
        CHECK (image_path IS NULL OR image_path ~ '^[A-Za-z0-9/_-]{1,200}\\.(jpg|png|webp)$'),
      CONSTRAINT ck_menu_items_sort CHECK (sort_order BETWEEN 0 AND 100000)
    )
    """,
    "CREATE INDEX ix_menu_items_category ON menu_items (restaurant_id, category_id, sort_order)",
    """
    CREATE TABLE menu_modifier_groups (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      name text NOT NULL,
      min_select smallint NOT NULL DEFAULT 0,
      max_select smallint NOT NULL DEFAULT 1,
      sort_order integer NOT NULL DEFAULT 0,
      deleted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_menu_modifier_groups_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT ck_menu_modifier_groups_name CHECK (char_length(name) BETWEEN 1 AND 60),
      CONSTRAINT ck_menu_modifier_groups_select
        CHECK (min_select >= 0 AND max_select >= 1 AND min_select <= max_select
               AND max_select <= 20),
      CONSTRAINT ck_menu_modifier_groups_sort CHECK (sort_order BETWEEN 0 AND 100000)
    )
    """,
    """
    CREATE TABLE menu_modifiers (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      group_id uuid NOT NULL,
      name text NOT NULL,
      display_price_delta_paise integer,
      is_available boolean NOT NULL DEFAULT true,
      sort_order integer NOT NULL DEFAULT 0,
      deleted_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_menu_modifiers_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT fk_menu_modifiers_group FOREIGN KEY (group_id, restaurant_id)
        REFERENCES menu_modifier_groups (id, restaurant_id),
      CONSTRAINT ck_menu_modifiers_name CHECK (char_length(name) BETWEEN 1 AND 60),
      CONSTRAINT ck_menu_modifiers_price CHECK (
        display_price_delta_paise IS NULL
        OR display_price_delta_paise BETWEEN -100000000 AND 100000000),
      CONSTRAINT ck_menu_modifiers_sort CHECK (sort_order BETWEEN 0 AND 100000)
    )
    """,
    "CREATE INDEX ix_menu_modifiers_group ON menu_modifiers (group_id)",
    """
    CREATE TABLE menu_item_modifier_groups (
      menu_item_id uuid NOT NULL,
      group_id uuid NOT NULL,
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      sort_order integer NOT NULL DEFAULT 0,
      CONSTRAINT pk_menu_item_modifier_groups PRIMARY KEY (menu_item_id, group_id),
      CONSTRAINT fk_menu_item_modifier_groups_item FOREIGN KEY (menu_item_id, restaurant_id)
        REFERENCES menu_items (id, restaurant_id),
      CONSTRAINT fk_menu_item_modifier_groups_group FOREIGN KEY (group_id, restaurant_id)
        REFERENCES menu_modifier_groups (id, restaurant_id),
      CONSTRAINT ck_menu_item_modifier_groups_sort CHECK (sort_order BETWEEN 0 AND 100000)
    )
    """,
    # --- orders ----------------------------------------------------------------------------------
    """
    CREATE TABLE orders (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      order_number bigint NOT NULL,
      table_id uuid NOT NULL,
      source text NOT NULL,
      table_session_id uuid,
      placed_by_user_id uuid,
      status text NOT NULL DEFAULT 'placed',
      version integer NOT NULL DEFAULT 1,
      notes text,
      idempotency_key text NOT NULL,
      request_fingerprint bytea NOT NULL,
      cancel_requested_at timestamptz,
      cancel_request_reason text,
      cancel_requested_by text,
      cancel_requested_by_user_id uuid,
      cancelled_at timestamptz,
      cancel_reason_code text,
      cancel_reason text,
      cancelled_by_user_id uuid,
      accepted_at timestamptz,
      preparing_at timestamptz,
      ready_at timestamptz,
      served_at timestamptz,
      completed_at timestamptz,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_orders_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT uq_orders_number UNIQUE (restaurant_id, order_number),
      CONSTRAINT fk_orders_table FOREIGN KEY (table_id, restaurant_id)
        REFERENCES dining_tables (id, restaurant_id),
      CONSTRAINT fk_orders_table_session FOREIGN KEY (table_session_id, table_id, restaurant_id)
        REFERENCES table_sessions (id, table_id, restaurant_id),
      CONSTRAINT fk_orders_placed_by FOREIGN KEY (placed_by_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT fk_orders_cancel_requested_by
        FOREIGN KEY (cancel_requested_by_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT fk_orders_cancelled_by FOREIGN KEY (cancelled_by_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT ck_orders_number CHECK (order_number > 0),
      CONSTRAINT ck_orders_source CHECK (
        (source = 'customer' AND table_session_id IS NOT NULL AND placed_by_user_id IS NULL)
        OR (source = 'waiter' AND placed_by_user_id IS NOT NULL AND table_session_id IS NULL)),
      CONSTRAINT ck_orders_status CHECK (status IN (
        'placed', 'accepted', 'preparing', 'ready', 'served', 'completed', 'cancelled')),
      CONSTRAINT ck_orders_version CHECK (version >= 1),
      CONSTRAINT ck_orders_notes CHECK (notes IS NULL OR char_length(notes) <= 500),
      CONSTRAINT ck_orders_idempotency_key CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{8,64}$'),
      CONSTRAINT ck_orders_fingerprint CHECK (octet_length(request_fingerprint) = 32),
      CONSTRAINT ck_orders_cancel_requested_by
        CHECK (cancel_requested_by IS NULL OR cancel_requested_by IN ('customer', 'staff')),
      CONSTRAINT ck_orders_cancel_request_reason
        CHECK (cancel_request_reason IS NULL OR char_length(cancel_request_reason) <= 300),
      CONSTRAINT ck_orders_cancel_reason
        CHECK (cancel_reason IS NULL OR char_length(cancel_reason) <= 300),
      CONSTRAINT ck_orders_cancel_reason_code CHECK (
        cancel_reason_code IS NULL OR cancel_reason_code IN (
          'customer_changed_mind', 'item_unavailable', 'duplicate', 'kitchen_issue', 'other')),
      CONSTRAINT ck_orders_cancelled CHECK ((status = 'cancelled') = (cancelled_at IS NOT NULL))
    )
    """,
    "CREATE UNIQUE INDEX uq_orders_idempotency_session "
    "ON orders (restaurant_id, table_session_id, idempotency_key) "
    "WHERE table_session_id IS NOT NULL",
    "CREATE UNIQUE INDEX uq_orders_idempotency_user "
    "ON orders (restaurant_id, placed_by_user_id, idempotency_key) "
    "WHERE placed_by_user_id IS NOT NULL",
    "CREATE INDEX ix_orders_status ON orders (restaurant_id, status, created_at)",
    "CREATE INDEX ix_orders_table ON orders (restaurant_id, table_id, created_at DESC)",
    "CREATE INDEX ix_orders_table_session ON orders (table_session_id) "
    "WHERE table_session_id IS NOT NULL",
    """
    CREATE TABLE order_items (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      order_id uuid NOT NULL,
      line_no smallint NOT NULL,
      menu_item_id uuid NOT NULL,
      item_name text NOT NULL,
      quantity smallint NOT NULL,
      notes text,
      display_price_paise integer,
      created_at timestamptz NOT NULL DEFAULT now(),
      CONSTRAINT uq_order_items_id_restaurant UNIQUE (id, restaurant_id),
      CONSTRAINT uq_order_items_line UNIQUE (order_id, line_no),
      CONSTRAINT fk_order_items_order FOREIGN KEY (order_id, restaurant_id)
        REFERENCES orders (id, restaurant_id),
      CONSTRAINT fk_order_items_menu_item FOREIGN KEY (menu_item_id, restaurant_id)
        REFERENCES menu_items (id, restaurant_id),
      CONSTRAINT ck_order_items_line_no CHECK (line_no BETWEEN 1 AND 30),
      CONSTRAINT ck_order_items_quantity CHECK (quantity BETWEEN 1 AND 50),
      CONSTRAINT ck_order_items_name CHECK (char_length(item_name) BETWEEN 1 AND 80),
      CONSTRAINT ck_order_items_notes CHECK (notes IS NULL OR char_length(notes) <= 200),
      CONSTRAINT ck_order_items_price
        CHECK (display_price_paise IS NULL OR display_price_paise BETWEEN 0 AND 100000000)
    )
    """,
    "CREATE INDEX ix_order_items_order ON order_items (order_id)",
    """
    CREATE TABLE order_item_modifiers (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      order_item_id uuid NOT NULL,
      modifier_id uuid NOT NULL,
      group_name text NOT NULL,
      modifier_name text NOT NULL,
      display_price_delta_paise integer,
      CONSTRAINT uq_order_item_modifiers_item_modifier UNIQUE (order_item_id, modifier_id),
      CONSTRAINT fk_order_item_modifiers_item FOREIGN KEY (order_item_id, restaurant_id)
        REFERENCES order_items (id, restaurant_id),
      CONSTRAINT fk_order_item_modifiers_modifier FOREIGN KEY (modifier_id, restaurant_id)
        REFERENCES menu_modifiers (id, restaurant_id),
      CONSTRAINT ck_order_item_modifiers_group_name
        CHECK (char_length(group_name) BETWEEN 1 AND 60),
      CONSTRAINT ck_order_item_modifiers_name CHECK (char_length(modifier_name) BETWEEN 1 AND 60)
    )
    """,
    """
    CREATE TABLE order_events (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      seq bigint GENERATED ALWAYS AS IDENTITY,
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      order_id uuid NOT NULL,
      type text NOT NULL,
      from_status text,
      to_status text,
      order_version integer NOT NULL,
      actor_type text NOT NULL,
      actor_user_id uuid,
      actor_table_session_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(),
      payload jsonb NOT NULL DEFAULT '{}'::jsonb,
      CONSTRAINT fk_order_events_order FOREIGN KEY (order_id, restaurant_id)
        REFERENCES orders (id, restaurant_id),
      CONSTRAINT fk_order_events_actor_user FOREIGN KEY (actor_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT fk_order_events_actor_session FOREIGN KEY (actor_table_session_id, restaurant_id)
        REFERENCES table_sessions (id, restaurant_id),
      CONSTRAINT ck_order_events_type CHECK (type IN ('order.created', 'order.status_changed',
        'order.cancel_requested', 'order.cancel_request_rejected', 'order.cancelled')),
      CONSTRAINT ck_order_events_actor_type CHECK (actor_type IN ('customer', 'staff', 'system')),
      CONSTRAINT ck_order_events_payload CHECK (jsonb_typeof(payload) = 'object')
    )
    """,
    "CREATE INDEX ix_order_events_restaurant_seq ON order_events (restaurant_id, seq)",
    "CREATE INDEX ix_order_events_order ON order_events (order_id)",
    # --- audit -----------------------------------------------------------------------------------
    """
    CREATE TABLE audit_logs (
      id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      restaurant_id uuid NOT NULL REFERENCES restaurants (id),
      occurred_at timestamptz NOT NULL DEFAULT now(),
      actor_type text NOT NULL,
      actor_user_id uuid,
      actor_table_session_id uuid,
      action text NOT NULL,
      entity_type text,
      entity_id text,
      details jsonb NOT NULL DEFAULT '{}'::jsonb,
      ip inet,
      request_id text,
      CONSTRAINT fk_audit_logs_actor_user FOREIGN KEY (actor_user_id, restaurant_id)
        REFERENCES users (id, restaurant_id),
      CONSTRAINT ck_audit_logs_actor_type
        CHECK (actor_type IN ('staff', 'customer', 'system', 'anonymous')),
      CONSTRAINT ck_audit_logs_action
        CHECK (char_length(action) <= 64 AND action ~ '^[a-z_]+(\\.[a-z_]+)+$'),
      CONSTRAINT ck_audit_logs_entity_type
        CHECK (entity_type IS NULL OR char_length(entity_type) <= 40),
      CONSTRAINT ck_audit_logs_entity_id CHECK (entity_id IS NULL OR char_length(entity_id) <= 64),
      CONSTRAINT ck_audit_logs_request_id
        CHECK (request_id IS NULL OR char_length(request_id) <= 64),
      CONSTRAINT ck_audit_logs_details CHECK (jsonb_typeof(details) = 'object')
    )
    """,
    "CREATE INDEX ix_audit_logs_restaurant_time ON audit_logs (restaurant_id, occurred_at DESC)",
    # --- pre-tenant resolver functions (SECURITY DEFINER, routing ids only) ----------------------
    """
    CREATE FUNCTION app_resolve_restaurant_code(p_code text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
      SELECT r.id FROM public.restaurants r
      WHERE r.code = p_code AND r.status = 'active'
    $$
    """,
    """
    CREATE FUNCTION app_resolve_qr_token(p_token text)
    RETURNS TABLE (restaurant_id uuid, table_id uuid)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
      SELECT q.restaurant_id, q.table_id
      FROM public.table_qr_tokens q
      JOIN public.dining_tables t ON t.id = q.table_id AND t.restaurant_id = q.restaurant_id
      JOIN public.restaurants r ON r.id = q.restaurant_id
      WHERE q.token = p_token AND q.revoked_at IS NULL
        AND t.is_active AND t.deleted_at IS NULL AND r.status = 'active'
    $$
    """,
    """
    CREATE FUNCTION app_resolve_table_session(p_token_hash bytea) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
      SELECT s.restaurant_id FROM public.table_sessions s
      JOIN public.restaurants r ON r.id = s.restaurant_id
      WHERE s.token_hash = p_token_hash AND s.status = 'active' AND s.expires_at > now()
        AND r.status = 'active'
    $$
    """,
    """
    CREATE FUNCTION app_resolve_refresh_token(p_token_hash bytea) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
      SELECT rt.restaurant_id FROM public.refresh_tokens rt WHERE rt.token_hash = p_token_hash
    $$
    """,
    """
    CREATE FUNCTION app_resolve_password_reset_token(p_token_hash bytea) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
      SELECT t.restaurant_id FROM public.password_reset_tokens t
      WHERE t.token_hash = p_token_hash AND t.used_at IS NULL AND t.expires_at > now()
    $$
    """,
    """
    CREATE FUNCTION app_due_pin_rotations(p_now timestamptz) RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
    AS $$
      SELECT r.id FROM public.restaurants r
      JOIN public.pin_versions p ON p.restaurant_id = r.id AND p.superseded_at IS NULL
      WHERE r.status = 'active' AND r.pin_rotation_minutes IS NOT NULL
        AND p.created_at + make_interval(mins => r.pin_rotation_minutes) <= p_now
    $$
    """,
]


def _exec(sql: str) -> None:
    # exec_driver_sql: no bind-parameter parsing, statements run exactly as written.
    op.get_bind().exec_driver_sql(sql)


def _app_role() -> str:
    role = context.config.attributes.get("app_db_role") or get_settings().app_db_role
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("APP_DB_ROLE must be a plain lowercase identifier")
    return role


def upgrade() -> None:
    app_role = _app_role()

    for statement in SCHEMA:
        _exec(statement)

    for table in UPDATED_AT_TABLES:
        _exec(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION app_touch_updated_at()"
        )

    for table, columns in IMMUTABLE_COLUMNS.items():
        args = ", ".join(f"'{c}'" for c in columns)
        _exec(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION app_immutable_columns({args})"
        )

    # Row Level Security: fail-closed tenant isolation.
    _exec("ALTER TABLE restaurants ENABLE ROW LEVEL SECURITY")
    _exec(
        "CREATE POLICY tenant_isolation ON restaurants "
        "USING (id = app_current_restaurant_id()) "
        "WITH CHECK (id = app_current_restaurant_id())"
    )
    for table in TENANT_TABLES:
        _exec(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        _exec(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (restaurant_id = app_current_restaurant_id()) "
            "WITH CHECK (restaurant_id = app_current_restaurant_id())"
        )

    # Least-privilege grants for the runtime role.
    _exec(f'GRANT USAGE ON SCHEMA public TO "{app_role}"')
    for privileges, tables in GRANTS.items():
        _exec(f'GRANT {privileges} ON {", ".join(tables)} TO "{app_role}"')

    for fn in RESOLVER_FUNCTIONS:
        _exec(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        _exec(f'GRANT EXECUTE ON FUNCTION {fn} TO "{app_role}"')


def downgrade() -> None:
    for fn in RESOLVER_FUNCTIONS:
        _exec(f"DROP FUNCTION IF EXISTS {fn}")
    for table in (
        "audit_logs",
        "order_events",
        "order_item_modifiers",
        "order_items",
        "orders",
        "menu_item_modifier_groups",
        "menu_modifiers",
        "menu_modifier_groups",
        "menu_items",
        "menu_categories",
        "table_sessions",
        "pin_versions",
        "table_qr_tokens",
        "dining_tables",
        "password_reset_tokens",
        "refresh_tokens",
        "auth_sessions",
        "users",
        "restaurants",
    ):
        _exec(f"DROP TABLE IF EXISTS {table}")
    for fn in ("app_immutable_columns()", "app_touch_updated_at()", "app_current_restaurant_id()"):
        _exec(f"DROP FUNCTION IF EXISTS {fn}")
