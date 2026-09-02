"""Create authoritative finance-domain persistence.

Revision ID: 20260902_0002
Revises: 20260901_0001
Created: 2026-09-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260902_0002"
down_revision: str | None = "20260901_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create finance tables, immutable history, and deferred invariants."""

    op.execute(
        """
        CREATE TABLE finance.profiles (
            id uuid PRIMARY KEY,
            locale text NOT NULL CHECK (locale = 'it-IT'),
            timezone text NOT NULL CHECK (timezone = 'Europe/Rome'),
            currency char(3) NOT NULL CHECK (currency = 'EUR'),
            default_funding_source_id uuid,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE UNIQUE INDEX profiles_single_active
            ON finance.profiles (active) WHERE active;

        CREATE TABLE finance.categories (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            name text NOT NULL CHECK (btrim(name) <> ''),
            normalized_name text NOT NULL CHECK (btrim(normalized_name) <> ''),
            description text NOT NULL CHECK (btrim(description) <> ''),
            parent_id uuid REFERENCES finance.categories(id),
            status text NOT NULL CHECK (status IN ('active', 'archived')),
            seeded boolean NOT NULL DEFAULT false,
            taxonomy_version text,
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            CHECK (parent_id IS NULL OR parent_id <> id)
        );
        CREATE UNIQUE INDEX categories_active_name
            ON finance.categories (profile_id, normalized_name)
            WHERE status = 'active';
        CREATE INDEX categories_parent ON finance.categories (profile_id, parent_id);

        CREATE TABLE finance.category_aliases (
            category_id uuid NOT NULL REFERENCES finance.categories(id),
            alias text NOT NULL CHECK (btrim(alias) <> ''),
            normalized_alias text NOT NULL CHECK (btrim(normalized_alias) <> ''),
            PRIMARY KEY (category_id, normalized_alias)
        );
        CREATE INDEX category_aliases_lookup
            ON finance.category_aliases (normalized_alias);

        CREATE TABLE finance.funding_sources (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            name text NOT NULL CHECK (btrim(name) <> ''),
            normalized_name text NOT NULL CHECK (btrim(normalized_name) <> ''),
            funding_class text NOT NULL
                CHECK (funding_class IN ('personal_funds', 'benefit', 'other')),
            balance_mode text NOT NULL
                CHECK (balance_mode IN ('untracked', 'ledger')),
            status text NOT NULL CHECK (status IN ('active', 'archived')),
            is_default boolean NOT NULL DEFAULT false,
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            UNIQUE (profile_id, normalized_name)
        );
        CREATE UNIQUE INDEX funding_sources_one_default
            ON finance.funding_sources (profile_id) WHERE is_default;
        ALTER TABLE finance.profiles
            ADD CONSTRAINT profiles_default_funding_source_fk
            FOREIGN KEY (default_funding_source_id)
            REFERENCES finance.funding_sources(id);

        CREATE TABLE finance.receipts (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            content_sha256 char(64) NOT NULL
                CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
            media_type text NOT NULL
                CHECK (media_type IN ('image/jpeg', 'image/png', 'image/webp')),
            byte_size bigint NOT NULL CHECK (byte_size > 0),
            stored_at timestamptz NOT NULL,
            source_reference text NOT NULL CHECK (btrim(source_reference) <> ''),
            status text NOT NULL
                CHECK (status IN ('stored', 'needs_review', 'approved', 'deleted')),
            original_filename text,
            UNIQUE (profile_id, content_sha256),
            CHECK (
                original_filename IS NULL
                OR (
                    btrim(original_filename) <> ''
                    AND position('/' IN original_filename) = 0
                    AND position(chr(92) IN original_filename) = 0
                )
            )
        );

        CREATE TABLE finance.expenses (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            amount_minor bigint NOT NULL CHECK (amount_minor <> 0),
            currency char(3) NOT NULL CHECK (currency = 'EUR'),
            occurred_at timestamptz NOT NULL,
            description text NOT NULL CHECK (btrim(description) <> ''),
            merchant text,
            status text NOT NULL
                CHECK (status IN ('draft', 'needs_review', 'posted', 'voided')),
            source_reference text NOT NULL CHECK (btrim(source_reference) <> ''),
            idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
            original_expense_id uuid REFERENCES finance.expenses(id),
            receipt_id uuid REFERENCES finance.receipts(id),
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            UNIQUE (profile_id, idempotency_key),
            CHECK (original_expense_id IS NULL OR amount_minor < 0),
            CHECK (original_expense_id IS NULL OR original_expense_id <> id)
        );
        CREATE INDEX expenses_profile_occurrence
            ON finance.expenses (profile_id, occurred_at);
        CREATE INDEX expenses_profile_status
            ON finance.expenses (profile_id, status);

        CREATE TABLE finance.category_allocations (
            expense_id uuid NOT NULL REFERENCES finance.expenses(id),
            category_id uuid NOT NULL REFERENCES finance.categories(id),
            amount_minor bigint NOT NULL CHECK (amount_minor <> 0),
            currency char(3) NOT NULL CHECK (currency = 'EUR'),
            provenance text NOT NULL CHECK (btrim(provenance) <> ''),
            evidence text,
            owner_override boolean NOT NULL DEFAULT false,
            receipt_line_id uuid,
            PRIMARY KEY (expense_id, category_id)
        );
        CREATE INDEX category_allocations_category
            ON finance.category_allocations (category_id, expense_id);

        CREATE TABLE finance.payment_allocations (
            expense_id uuid NOT NULL REFERENCES finance.expenses(id),
            funding_source_id uuid NOT NULL REFERENCES finance.funding_sources(id),
            amount_minor bigint NOT NULL CHECK (amount_minor <> 0),
            currency char(3) NOT NULL CHECK (currency = 'EUR'),
            PRIMARY KEY (expense_id, funding_source_id)
        );
        CREATE INDEX payment_allocations_source
            ON finance.payment_allocations (funding_source_id, expense_id);

        CREATE TABLE finance.wallet_entries (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            funding_source_id uuid NOT NULL REFERENCES finance.funding_sources(id),
            entry_type text NOT NULL
                CHECK (entry_type IN (
                    'opening', 'credit', 'debit', 'refund', 'expiry', 'adjustment'
                )),
            amount_minor bigint NOT NULL CHECK (amount_minor <> 0),
            currency char(3) NOT NULL CHECK (currency = 'EUR'),
            occurred_at timestamptz NOT NULL,
            expires_on date,
            expense_id uuid REFERENCES finance.expenses(id),
            note text,
            created_at timestamptz NOT NULL,
            CHECK (
                entry_type = 'adjustment'
                OR (entry_type IN ('opening', 'credit', 'refund') AND amount_minor > 0)
                OR (entry_type IN ('debit', 'expiry') AND amount_minor < 0)
            ),
            CHECK (expires_on IS NULL OR entry_type IN ('opening', 'credit')),
            CHECK (expires_on IS NULL OR expires_on >= occurred_at::date)
        );
        CREATE INDEX wallet_entries_source_occurrence
            ON finance.wallet_entries (funding_source_id, occurred_at, id);

        CREATE TABLE finance.monthly_budgets (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            category_id uuid NOT NULL REFERENCES finance.categories(id),
            target_minor bigint NOT NULL CHECK (target_minor > 0),
            currency char(3) NOT NULL CHECK (currency = 'EUR'),
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            UNIQUE (profile_id, category_id)
        );

        CREATE TABLE finance.monthly_budget_overrides (
            budget_id uuid NOT NULL REFERENCES finance.monthly_budgets(id),
            year integer NOT NULL CHECK (year BETWEEN 1 AND 9999),
            month integer NOT NULL CHECK (month BETWEEN 1 AND 12),
            target_minor bigint NOT NULL CHECK (target_minor > 0),
            currency char(3) NOT NULL CHECK (currency = 'EUR'),
            PRIMARY KEY (budget_id, year, month)
        );

        CREATE TABLE finance.category_proposals (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            proposed_name text NOT NULL CHECK (btrim(proposed_name) <> ''),
            normalized_name text NOT NULL CHECK (btrim(normalized_name) <> ''),
            description text NOT NULL CHECK (btrim(description) <> ''),
            parent_id uuid REFERENCES finance.categories(id),
            rationale text NOT NULL CHECK (btrim(rationale) <> ''),
            alternative_category_ids uuid[] NOT NULL DEFAULT '{}',
            excluded_names text[] NOT NULL DEFAULT '{}',
            source_reference text NOT NULL CHECK (btrim(source_reference) <> ''),
            status text NOT NULL
                CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );

        CREATE TABLE finance.audit_events (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            aggregate_type text NOT NULL CHECK (btrim(aggregate_type) <> ''),
            aggregate_id uuid NOT NULL,
            action text NOT NULL CHECK (btrim(action) <> ''),
            actor text NOT NULL CHECK (btrim(actor) <> ''),
            source_reference text NOT NULL CHECK (btrim(source_reference) <> ''),
            occurred_at timestamptz NOT NULL,
            prior_version integer CHECK (prior_version > 0),
            current_version integer NOT NULL CHECK (current_version > 0),
            reason text NOT NULL CHECK (btrim(reason) <> ''),
            CHECK (prior_version IS NULL OR current_version > prior_version)
        );
        CREATE INDEX audit_events_aggregate
            ON finance.audit_events (aggregate_type, aggregate_id, occurred_at);

        CREATE TABLE finance.idempotency_records (
            profile_id uuid NOT NULL REFERENCES finance.profiles(id),
            idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
            command_type text NOT NULL CHECK (btrim(command_type) <> ''),
            request_hash char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
            aggregate_id uuid NOT NULL,
            created_at timestamptz NOT NULL,
            PRIMARY KEY (profile_id, idempotency_key)
        );
        """
    )
    op.execute(
        """
        CREATE FUNCTION finance.validate_category_hierarchy()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent_profile uuid;
            parent_parent uuid;
            parent_status text;
        BEGIN
            IF NEW.parent_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT profile_id, parent_id, status
              INTO parent_profile, parent_parent, parent_status
              FROM finance.categories
             WHERE id = NEW.parent_id;
            IF NOT FOUND OR parent_profile <> NEW.profile_id THEN
                RAISE EXCEPTION 'category parent must exist in the same profile';
            END IF;
            IF parent_parent IS NOT NULL THEN
                RAISE EXCEPTION 'category hierarchy cannot exceed two levels';
            END IF;
            IF NEW.status = 'active' AND parent_status <> 'active' THEN
                RAISE EXCEPTION 'active category requires an active parent';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER categories_validate_hierarchy
            BEFORE INSERT OR UPDATE OF profile_id, parent_id, status
            ON finance.categories
            FOR EACH ROW EXECUTE FUNCTION finance.validate_category_hierarchy();

        CREATE FUNCTION finance.validate_profile_funding_source()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            source_profile uuid;
            source_status text;
        BEGIN
            IF NEW.default_funding_source_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT profile_id, status INTO source_profile, source_status
              FROM finance.funding_sources
             WHERE id = NEW.default_funding_source_id;
            IF NOT FOUND OR source_profile <> NEW.id OR source_status <> 'active' THEN
                RAISE EXCEPTION 'default funding source must be active in the profile';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE CONSTRAINT TRIGGER profiles_validate_default_source
            AFTER INSERT OR UPDATE OF default_funding_source_id
            ON finance.profiles DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION finance.validate_profile_funding_source();

        CREATE FUNCTION finance.validate_expense_reconciliation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            target_id uuid;
            parent_record finance.expenses%ROWTYPE;
            category_total bigint;
            payment_total bigint;
            invalid_reference_count bigint;
        BEGIN
            IF TG_TABLE_NAME = 'expenses' THEN
                target_id := NEW.id;
            ELSIF TG_OP = 'DELETE' THEN
                target_id := OLD.expense_id;
            ELSE
                target_id := NEW.expense_id;
            END IF;
            SELECT * INTO parent_record FROM finance.expenses WHERE id = target_id;
            IF NOT FOUND OR parent_record.status NOT IN ('posted', 'voided') THEN
                RETURN NULL;
            END IF;
            SELECT COALESCE(sum(amount_minor), 0) INTO category_total
              FROM finance.category_allocations WHERE expense_id = target_id;
            SELECT COALESCE(sum(amount_minor), 0) INTO payment_total
              FROM finance.payment_allocations WHERE expense_id = target_id;
            IF category_total <> parent_record.amount_minor THEN
                RAISE EXCEPTION
                    'category allocations do not reconcile for expense %', target_id;
            END IF;
            IF payment_total <> parent_record.amount_minor THEN
                RAISE EXCEPTION
                    'payment allocations do not reconcile for expense %', target_id;
            END IF;
            SELECT count(*) INTO invalid_reference_count
              FROM finance.category_allocations allocation
              JOIN finance.categories category ON category.id = allocation.category_id
             WHERE allocation.expense_id = target_id
               AND (
                    category.profile_id <> parent_record.profile_id
                    OR (parent_record.status = 'posted' AND category.status <> 'active')
               );
            IF invalid_reference_count > 0 THEN
                RAISE EXCEPTION 'expense category reference is not active in profile';
            END IF;
            SELECT count(*) INTO invalid_reference_count
              FROM finance.payment_allocations allocation
              JOIN finance.funding_sources source
                ON source.id = allocation.funding_source_id
             WHERE allocation.expense_id = target_id
               AND (
                    source.profile_id <> parent_record.profile_id
                    OR (parent_record.status = 'posted' AND source.status <> 'active')
               );
            IF invalid_reference_count > 0 THEN
                RAISE EXCEPTION 'expense funding source is not active in profile';
            END IF;
            IF EXISTS (
                SELECT 1 FROM finance.category_allocations
                 WHERE expense_id = target_id
                   AND ((amount_minor > 0) <> (parent_record.amount_minor > 0))
            ) OR EXISTS (
                SELECT 1 FROM finance.payment_allocations
                 WHERE expense_id = target_id
                   AND ((amount_minor > 0) <> (parent_record.amount_minor > 0))
            ) THEN
                RAISE EXCEPTION 'allocation polarity differs from expense %', target_id;
            END IF;
            RETURN NULL;
        END;
        $$;
        CREATE CONSTRAINT TRIGGER expenses_reconcile
            AFTER INSERT OR UPDATE OF amount_minor, currency, status
            ON finance.expenses DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION finance.validate_expense_reconciliation();
        CREATE CONSTRAINT TRIGGER category_allocations_reconcile
            AFTER INSERT OR UPDATE OR DELETE ON finance.category_allocations
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION finance.validate_expense_reconciliation();
        CREATE CONSTRAINT TRIGGER payment_allocations_reconcile
            AFTER INSERT OR UPDATE OR DELETE ON finance.payment_allocations
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION finance.validate_expense_reconciliation();

        CREATE FUNCTION finance.prevent_immutable_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME;
        END;
        $$;
        CREATE TRIGGER audit_events_immutable
            BEFORE UPDATE OR DELETE ON finance.audit_events
            FOR EACH ROW EXECUTE FUNCTION finance.prevent_immutable_change();
        CREATE TRIGGER wallet_entries_immutable
            BEFORE UPDATE OR DELETE ON finance.wallet_entries
            FOR EACH ROW EXECUTE FUNCTION finance.prevent_immutable_change();
        """
    )


def downgrade() -> None:
    """Refuse destructive rollback after authoritative tables exist.

    Raises:
        RuntimeError: Always, because finance history must be restored or migrated
            forward rather than silently dropped.
    """

    raise RuntimeError(
        "finance persistence downgrade is destructive; restore an isolated backup "
        "or migrate forward"
    )
