"""Create durable Telegram messaging state.

Revision ID: 20260909_0005
Revises: 20260903_0004
Created: 2026-09-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260909_0005"
down_revision: str | None = "20260903_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create normalized inbox, offset, outbox, pairing, and action tables."""

    op.execute(
        """
        CREATE TABLE messaging.telegram_offsets (
            bot_key text PRIMARY KEY
                CHECK (bot_key ~ '^[a-z0-9][a-z0-9:-]{0,63}$'),
            environment text NOT NULL
                CHECK (environment IN ('production', 'test')),
            next_offset bigint NOT NULL DEFAULT 0 CHECK (next_offset >= 0),
            last_observed_at timestamptz,
            last_poll_at timestamptz,
            rejected_update_count bigint NOT NULL DEFAULT 0
                CHECK (rejected_update_count >= 0),
            unsupported_update_count bigint NOT NULL DEFAULT 0
                CHECK (unsupported_update_count >= 0),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );

        CREATE TABLE messaging.telegram_pairing_codes (
            id uuid PRIMARY KEY,
            bot_key text NOT NULL
                REFERENCES messaging.telegram_offsets(bot_key),
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            code_hash char(64) NOT NULL
                CHECK (code_hash ~ '^[0-9a-f]{64}$'),
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL,
            used_at timestamptz,
            revoked_at timestamptz,
            CHECK (expires_at > created_at),
            CHECK (used_at IS NULL OR used_at >= created_at),
            CHECK (revoked_at IS NULL OR revoked_at >= created_at),
            CHECK (used_at IS NULL OR revoked_at IS NULL)
        );
        CREATE UNIQUE INDEX telegram_pairing_codes_one_live
            ON messaging.telegram_pairing_codes(bot_key)
            WHERE used_at IS NULL AND revoked_at IS NULL;
        CREATE UNIQUE INDEX telegram_pairing_codes_hash
            ON messaging.telegram_pairing_codes(bot_key, code_hash);

        CREATE TABLE messaging.telegram_owner_bindings (
            bot_key text PRIMARY KEY
                REFERENCES messaging.telegram_offsets(bot_key),
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            telegram_user_id bigint NOT NULL CHECK (telegram_user_id > 0),
            telegram_chat_id bigint NOT NULL CHECK (telegram_chat_id > 0),
            paired_at timestamptz NOT NULL,
            UNIQUE (bot_key, telegram_user_id, telegram_chat_id)
        );

        CREATE TABLE messaging.telegram_inbox (
            id uuid PRIMARY KEY,
            bot_key text NOT NULL
                REFERENCES messaging.telegram_offsets(bot_key),
            update_id bigint NOT NULL CHECK (update_id >= 0),
            source_reference text NOT NULL
                CHECK (btrim(source_reference) <> ''),
            telegram_user_id bigint NOT NULL CHECK (telegram_user_id > 0),
            telegram_chat_id bigint NOT NULL CHECK (telegram_chat_id > 0),
            telegram_message_id bigint CHECK (telegram_message_id > 0),
            received_at timestamptz NOT NULL,
            accepted_at timestamptz NOT NULL,
            text_content text,
            callback_action text,
            callback_query_id text,
            reply_to_message_id bigint CHECK (reply_to_message_id > 0),
            has_image boolean NOT NULL DEFAULT false,
            telegram_file_id text,
            telegram_file_unique_id text,
            file_media_type text,
            file_size bigint,
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN (
                    'pending', 'processing', 'retry', 'completed', 'dead_letter'
                )),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            replay_count integer NOT NULL DEFAULT 0 CHECK (replay_count >= 0),
            next_attempt_at timestamptz NOT NULL,
            claim_token uuid,
            claimed_until timestamptz,
            last_error_code text,
            workflow_status text,
            workflow_result jsonb,
            completed_at timestamptz,
            UNIQUE (bot_key, update_id),
            UNIQUE (source_reference),
            CHECK (
                text_content IS NOT NULL
                OR callback_action IS NOT NULL
                OR has_image
            ),
            CHECK (
                NOT has_image
                OR (
                    telegram_file_id IS NOT NULL
                    AND telegram_file_unique_id IS NOT NULL
                )
            ),
            CHECK (
                file_media_type IS NULL
                OR file_media_type IN ('image/jpeg', 'image/png', 'image/webp')
            ),
            CHECK (
                file_size IS NULL
                OR (file_size > 0 AND file_size <= 20971520)
            ),
            CHECK (
                (status = 'processing')
                = (claim_token IS NOT NULL AND claimed_until IS NOT NULL)
            ),
            CHECK (status <> 'completed' OR completed_at IS NOT NULL),
            CHECK (status <> 'dead_letter' OR last_error_code IS NOT NULL),
            CHECK (workflow_result IS NULL OR jsonb_typeof(workflow_result) = 'object')
        );
        CREATE INDEX telegram_inbox_order
            ON messaging.telegram_inbox(bot_key, update_id);
        CREATE INDEX telegram_inbox_status
            ON messaging.telegram_inbox(bot_key, status, next_attempt_at);

        CREATE TABLE messaging.telegram_outbox (
            id uuid PRIMARY KEY,
            bot_key text NOT NULL
                REFERENCES messaging.telegram_offsets(bot_key),
            inbox_id uuid REFERENCES messaging.telegram_inbox(id),
            telegram_chat_id bigint NOT NULL CHECK (telegram_chat_id > 0),
            logical_key text NOT NULL CHECK (btrim(logical_key) <> ''),
            response_kind text NOT NULL CHECK (btrim(response_kind) <> ''),
            text_content text NOT NULL CHECK (btrim(text_content) <> ''),
            buttons jsonb NOT NULL DEFAULT '[]'::jsonb
                CHECK (jsonb_typeof(buttons) = 'array'),
            reply_to_message_id bigint CHECK (reply_to_message_id > 0),
            content_hash char(64) NOT NULL
                CHECK (content_hash ~ '^[0-9a-f]{64}$'),
            status text NOT NULL DEFAULT 'pending'
                CHECK (status IN (
                    'pending', 'sending', 'retry', 'sent', 'dead_letter'
                )),
            attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            next_attempt_at timestamptz NOT NULL,
            claim_token uuid,
            claimed_until timestamptz,
            last_error_code text,
            telegram_message_id bigint CHECK (telegram_message_id > 0),
            created_at timestamptz NOT NULL,
            sent_at timestamptz,
            UNIQUE (bot_key, logical_key),
            CHECK (
                (status = 'sending')
                = (claim_token IS NOT NULL AND claimed_until IS NOT NULL)
            ),
            CHECK (
                status <> 'sent'
                OR (sent_at IS NOT NULL AND telegram_message_id IS NOT NULL)
            ),
            CHECK (status <> 'dead_letter' OR last_error_code IS NOT NULL)
        );
        CREATE INDEX telegram_outbox_delivery
            ON messaging.telegram_outbox(bot_key, status, next_attempt_at, created_at);

        CREATE TABLE messaging.telegram_pending_actions (
            id uuid PRIMARY KEY,
            bot_key text NOT NULL
                REFERENCES messaging.telegram_offsets(bot_key),
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            telegram_user_id bigint NOT NULL CHECK (telegram_user_id > 0),
            telegram_chat_id bigint NOT NULL CHECK (telegram_chat_id > 0),
            outbox_id uuid NOT NULL REFERENCES messaging.telegram_outbox(id),
            target_type text NOT NULL
                CHECK (target_type IN ('category_review', 'movement')),
            target_id uuid NOT NULL,
            allowed_actions text[] NOT NULL CHECK (cardinality(allowed_actions) > 0),
            status text NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'consumed', 'cancelled')),
            telegram_message_id bigint CHECK (telegram_message_id > 0),
            created_at timestamptz NOT NULL,
            consumed_at timestamptz,
            CHECK ((status = 'consumed') = (consumed_at IS NOT NULL))
        );
        CREATE INDEX telegram_pending_actions_callback
            ON messaging.telegram_pending_actions(
                bot_key, telegram_user_id, telegram_chat_id, status
            );
        CREATE INDEX telegram_pending_actions_reply
            ON messaging.telegram_pending_actions(
                bot_key, telegram_chat_id, telegram_message_id
            ) WHERE status = 'active';
        """
    )


def downgrade() -> None:
    """Refuse removal of durable messaging and recovery history."""

    raise RuntimeError(
        "destructive messaging downgrade is disabled; restore a reviewed backup "
        "or migrate forward"
    )
