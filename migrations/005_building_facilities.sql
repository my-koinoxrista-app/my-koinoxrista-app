BEGIN;

CREATE TABLE IF NOT EXISTS building_facilities (
    building_id TEXT PRIMARY KEY
        REFERENCES buildings(id)
        ON DELETE RESTRICT,

    elevator BOOLEAN,
    shared_water BOOLEAN,

    sewage TEXT
        CHECK (sewage IN ('NETWORK', 'SEPTIC', 'NONE', 'OTHER')),

    garden BOOLEAN,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS heating_systems (
    building_id TEXT PRIMARY KEY
        REFERENCES buildings(id)
        ON DELETE RESTRICT,

    fuel_type TEXT
        CHECK (
            fuel_type IN (
                'OIL',
                'NATURAL_GAS',
                'ELECTRIC',
                'OTHER',
                'NONE'
            )
        ),

    system_type TEXT
        CHECK (system_type IN ('CENTRAL', 'AUTONOMOUS', 'NONE')),

    metering_type TEXT
        CHECK (
            metering_type IN (
                'NONE',
                'HOUR_METER',
                'HEAT_METER',
                'OTHER'
            )
        ),

    allocation_method TEXT,
    study_reference TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT heating_systems_consistency_check
        CHECK (
            (system_type IS DISTINCT FROM 'NONE'
                OR fuel_type IS NULL
                OR fuel_type = 'NONE')
            AND
            (system_type IS DISTINCT FROM 'NONE'
                OR metering_type IS NULL
                OR metering_type = 'NONE')
            AND
            (fuel_type IS DISTINCT FROM 'NONE'
                OR system_type IS NULL
                OR system_type = 'NONE')
        )
);

COMMIT;
