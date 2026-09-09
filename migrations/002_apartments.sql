BEGIN;

CREATE TABLE IF NOT EXISTS apartments (
    id TEXT PRIMARY KEY,

    building_id TEXT NOT NULL
        REFERENCES buildings(id)
        ON DELETE RESTRICT,

    code TEXT NOT NULL,

    property_type TEXT NOT NULL DEFAULT 'APARTMENT'
        CHECK (
            property_type IN (
                'APARTMENT',
                'SHOP',
                'OFFICE',
                'STORAGE',
                'PARKING',
                'OTHER'
            )
        ),

    floor INTEGER,

    area_sqm NUMERIC(10, 2)
        CHECK (area_sqm > 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT apartments_building_code_unique
        UNIQUE (building_id, code)
);

CREATE INDEX IF NOT EXISTS idx_apartments_building_id
    ON apartments(building_id);

COMMIT;