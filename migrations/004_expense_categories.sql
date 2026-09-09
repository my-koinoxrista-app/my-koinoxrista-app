BEGIN;

CREATE TABLE IF NOT EXISTS allocation_rules (
    id TEXT PRIMARY KEY,

    building_id TEXT NOT NULL
        REFERENCES buildings(id)
        ON DELETE RESTRICT,

    name TEXT NOT NULL,

    rule_type TEXT NOT NULL
        CHECK (rule_type IN ('WEIGHTED', 'EQUAL', 'DIRECT')),

    allocation_table_id TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT allocation_rules_building_id_unique
        UNIQUE (building_id, id),

    CONSTRAINT allocation_rules_table_fk
        FOREIGN KEY (building_id, allocation_table_id)
        REFERENCES allocation_tables(building_id, id)
        ON DELETE RESTRICT,

    CONSTRAINT allocation_rules_type_check
        CHECK (
            (rule_type = 'WEIGHTED' AND allocation_table_id IS NOT NULL)
            OR
            (rule_type IN ('EQUAL', 'DIRECT') AND allocation_table_id IS NULL)
        )
);


CREATE TABLE IF NOT EXISTS allocation_rule_participants (
    rule_id TEXT NOT NULL,
    building_id TEXT NOT NULL,
    apartment_id TEXT NOT NULL,

    PRIMARY KEY (rule_id, apartment_id),

    CONSTRAINT allocation_rule_participants_rule_fk
        FOREIGN KEY (building_id, rule_id)
        REFERENCES allocation_rules(building_id, id)
        ON DELETE RESTRICT,

    CONSTRAINT allocation_rule_participants_apartment_fk
        FOREIGN KEY (building_id, apartment_id)
        REFERENCES apartments(building_id, id)
        ON DELETE RESTRICT
);


CREATE TABLE IF NOT EXISTS expense_categories (
    id TEXT PRIMARY KEY,

    building_id TEXT NOT NULL
        REFERENCES buildings(id)
        ON DELETE RESTRICT,

    name TEXT NOT NULL,

    default_rule_id TEXT NOT NULL,

    payer TEXT NOT NULL DEFAULT 'TENANT'
        CHECK (payer IN ('TENANT', 'OWNER', 'OTHER')),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT expense_categories_building_id_unique
        UNIQUE (building_id, id),

    CONSTRAINT expense_categories_rule_fk
        FOREIGN KEY (building_id, default_rule_id)
        REFERENCES allocation_rules(building_id, id)
        ON DELETE RESTRICT
);

COMMIT;