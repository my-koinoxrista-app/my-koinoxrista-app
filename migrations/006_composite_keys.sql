BEGIN;

-- Prevent concurrent writes while changing the key constraints.
LOCK TABLE
    apartments,
    allocation_tables,
    allocation_shares,
    allocation_rules,
    allocation_rule_participants,
    expense_categories
IN ACCESS EXCLUSIVE MODE;

-- Remove foreign keys that depend on the existing unique constraints.
ALTER TABLE allocation_shares
    DROP CONSTRAINT allocation_shares_table_fk,
    DROP CONSTRAINT allocation_shares_apartment_fk;

ALTER TABLE allocation_rule_participants
    DROP CONSTRAINT allocation_rule_participants_rule_fk,
    DROP CONSTRAINT allocation_rule_participants_apartment_fk;

ALTER TABLE allocation_rules
    DROP CONSTRAINT allocation_rules_table_fk;

ALTER TABLE expense_categories
    DROP CONSTRAINT expense_categories_rule_fk;

-- Replace globally unique primary keys with building-scoped keys.
ALTER TABLE apartments
    DROP CONSTRAINT apartments_pkey,
    ADD CONSTRAINT apartments_pkey PRIMARY KEY (building_id, id);

ALTER TABLE allocation_tables
    DROP CONSTRAINT allocation_tables_pkey,
    ADD CONSTRAINT allocation_tables_pkey PRIMARY KEY (building_id, id);

ALTER TABLE allocation_rules
    DROP CONSTRAINT allocation_rules_pkey,
    ADD CONSTRAINT allocation_rules_pkey PRIMARY KEY (building_id, id);

ALTER TABLE expense_categories
    DROP CONSTRAINT expense_categories_pkey,
    ADD CONSTRAINT expense_categories_pkey PRIMARY KEY (building_id, id);

-- Shares and participants must also include the building in their keys.
ALTER TABLE allocation_shares
    DROP CONSTRAINT allocation_shares_pkey,
    ADD CONSTRAINT allocation_shares_pkey
        PRIMARY KEY (building_id, allocation_table_id, apartment_id);

ALTER TABLE allocation_rule_participants
    DROP CONSTRAINT allocation_rule_participants_pkey,
    ADD CONSTRAINT allocation_rule_participants_pkey
        PRIMARY KEY (building_id, rule_id, apartment_id);

-- Restore foreign keys using building-scoped identifiers.
ALTER TABLE allocation_shares
    ADD CONSTRAINT allocation_shares_table_fk
        FOREIGN KEY (building_id, allocation_table_id)
        REFERENCES allocation_tables(building_id, id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT allocation_shares_apartment_fk
        FOREIGN KEY (building_id, apartment_id)
        REFERENCES apartments(building_id, id)
        ON DELETE RESTRICT;

ALTER TABLE allocation_rule_participants
    ADD CONSTRAINT allocation_rule_participants_rule_fk
        FOREIGN KEY (building_id, rule_id)
        REFERENCES allocation_rules(building_id, id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT allocation_rule_participants_apartment_fk
        FOREIGN KEY (building_id, apartment_id)
        REFERENCES apartments(building_id, id)
        ON DELETE RESTRICT;

ALTER TABLE allocation_rules
    ADD CONSTRAINT allocation_rules_table_fk
        FOREIGN KEY (building_id, allocation_table_id)
        REFERENCES allocation_tables(building_id, id)
        ON DELETE RESTRICT;

ALTER TABLE expense_categories
    ADD CONSTRAINT expense_categories_rule_fk
        FOREIGN KEY (building_id, default_rule_id)
        REFERENCES allocation_rules(building_id, id)
        ON DELETE RESTRICT;

COMMIT;