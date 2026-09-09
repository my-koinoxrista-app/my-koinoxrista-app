BEGIN;

ALTER TABLE apartments
    ADD CONSTRAINT apartments_building_id_unique
    UNIQUE (building_id, id);

CREATE TABLE IF NOT EXISTS allocation_tables (
    id TEXT PRIMARY KEY,

    building_id TEXT NOT NULL
        REFERENCES buildings(id)
        ON DELETE RESTRICT,

    name TEXT NOT NULL,

    expected_total NUMERIC(18, 6)
        CHECK (expected_total > 0),

    source_reference TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT allocation_tables_building_id_unique
        UNIQUE (building_id, id)
);

CREATE INDEX IF NOT EXISTS idx_allocation_tables_building_id
    ON allocation_tables(building_id);


CREATE TABLE IF NOT EXISTS allocation_shares (
    allocation_table_id TEXT NOT NULL,

    building_id TEXT NOT NULL,

    apartment_id TEXT NOT NULL,

    weight NUMERIC(18, 6) NOT NULL
        CHECK (weight >= 0),

    PRIMARY KEY (allocation_table_id, apartment_id),

    CONSTRAINT allocation_shares_table_fk
        FOREIGN KEY (building_id, allocation_table_id)
        REFERENCES allocation_tables(building_id, id)
        ON DELETE RESTRICT,

    CONSTRAINT allocation_shares_apartment_fk
        FOREIGN KEY (building_id, apartment_id)
        REFERENCES apartments(building_id, id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_allocation_shares_apartment_id
    ON allocation_shares(apartment_id);

COMMIT;