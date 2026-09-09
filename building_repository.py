"""PostgreSQL persistence for building configurations.

The JSONB configuration is currently the source of truth.
Relational configuration tables are synchronized in one transaction.
"""

import json
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from configuration import building_json, load_building
from allocation_configuration import SELECTION_KEY, active_category_ids
from database import get_connection
from property_metadata import property_names_for_save


def _sql_decimal(value, whole_digits, decimal_places, field):
    """Reject values that PostgreSQL would silently round."""

    if value is None:
        return None

    try:
        value = Decimal(str(value))
        unit = Decimal(1).scaleb(-decimal_places)

        if not value.is_finite():
            raise ValueError

        if value != value.quantize(unit):
            raise ValueError(
                f"{field} has more than {decimal_places} decimal places."
            )

        if abs(value) >= Decimal(10) ** whole_digits:
            raise ValueError(f"{field} exceeds the database numeric range.")

    except (InvalidOperation, TypeError):
        raise ValueError(f"Invalid numeric value for {field}.") from None

    return value


def _rule_id(building_id, category_id):
    """Return a stable ID for a category's default rule."""

    return str(
        uuid5(
            NAMESPACE_URL,
            f"koinoxrista:building:{building_id}:category:{category_id}:default-rule",
        )
    )


def _delete_missing(cursor, table, building_id, ids):
    """Delete configuration rows absent from the current building."""

    ids = list(ids)

    if ids:
        cursor.execute(
            f"""
            DELETE FROM {table}
            WHERE building_id = %s
              AND NOT (id = ANY(%s))
            """,
            (building_id, ids),
        )
    else:
        cursor.execute(
            f"DELETE FROM {table} WHERE building_id = %s",
            (building_id,),
        )


def _sync_apartments(cursor, building):
    """Insert or update properties without deleting obsolete ones yet."""

    for apartment in building.apartments:
        cursor.execute(
            """
            INSERT INTO apartments (
                building_id,
                id,
                code,
                property_type,
                floor,
                area_sqm
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (building_id, id) DO UPDATE SET
                code = EXCLUDED.code,
                property_type = EXCLUDED.property_type,
                floor = EXCLUDED.floor,
                area_sqm = EXCLUDED.area_sqm,
                updated_at = NOW()
            """,
            (
                building.id,
                apartment.id,
                apartment.code,
                apartment.property_type,
                apartment.floor,
                _sql_decimal(
                    apartment.area_sqm, 8, 2, "Apartment area"
                ),
            ),
        )


def _sync_allocation_tables(cursor, building):
    """Insert or update allocation tables and their shares."""

    for table in building.tables.values():
        cursor.execute(
            """
            INSERT INTO allocation_tables (
                building_id,
                id,
                name,
                expected_total,
                source_reference
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (building_id, id) DO UPDATE SET
                name = EXCLUDED.name,
                expected_total = EXCLUDED.expected_total,
                source_reference = EXCLUDED.source_reference,
                updated_at = NOW()
            """,
            (
                building.id,
                table.id,
                table.name,
                _sql_decimal(
                    table.expected_total, 12, 6, "Expected total"
                ),
                table.source_reference,
            ),
        )

        for apartment_id, weight in table.weights.items():
            cursor.execute(
                """
                INSERT INTO allocation_shares (
                    building_id,
                    allocation_table_id,
                    apartment_id,
                    weight
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (
                    building_id,
                    allocation_table_id,
                    apartment_id
                )
                DO UPDATE SET
                    weight = EXCLUDED.weight
                """,
                (
                    building.id,
                    table.id,
                    apartment_id,
                    _sql_decimal(
                        weight, 12, 6, "Allocation weight"
                    ),
                ),
            )


def _sync_rules_and_categories(cursor, building):
    """Synchronize category defaults and their allocation rules."""

    for category in building.categories.values():
        rule = category.rule
        rule_id = _rule_id(building.id, category.id)

        table_id = (
            rule.table_id
            if rule.type == "WEIGHTED"
            else None
        )

        cursor.execute(
            """
            INSERT INTO allocation_rules (
                building_id,
                id,
                name,
                rule_type,
                allocation_table_id
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (building_id, id) DO UPDATE SET
                name = EXCLUDED.name,
                rule_type = EXCLUDED.rule_type,
                allocation_table_id = EXCLUDED.allocation_table_id,
                updated_at = NOW()
            """,
            (
                building.id,
                rule_id,
                f"{category.name} - Default",
                rule.type,
                table_id,
            ),
        )

        if rule.type == "EQUAL":
            for apartment_id in rule.participants:
                cursor.execute(
                    """
                    INSERT INTO allocation_rule_participants (
                        building_id,
                        rule_id,
                        apartment_id
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (
                        building_id,
                        rule_id,
                        apartment_id
                    )
                    DO NOTHING
                    """,
                    (building.id, rule_id, apartment_id),
                )

        cursor.execute(
            """
            INSERT INTO expense_categories (
                building_id,
                id,
                name,
                default_rule_id,
                payer
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (building_id, id) DO UPDATE SET
                name = EXCLUDED.name,
                default_rule_id = EXCLUDED.default_rule_id,
                payer = EXCLUDED.payer,
                updated_at = NOW()
            """,
            (
                building.id,
                category.id,
                category.name,
                rule_id,
                category.payer,
            ),
        )


def _sync_facilities(cursor, building):
    """Synchronize optional facilities and heating metadata."""

    facilities = building.facilities
    heating = building.heating

    cursor.execute(
        """
        INSERT INTO building_facilities (
            building_id,
            elevator,
            shared_water,
            sewage,
            garden
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (building_id) DO UPDATE SET
            elevator = EXCLUDED.elevator,
            shared_water = EXCLUDED.shared_water,
            sewage = EXCLUDED.sewage,
            garden = EXCLUDED.garden,
            updated_at = NOW()
        """,
        (
            building.id,
            facilities.get("elevator"),
            facilities.get("shared_water"),
            facilities.get("sewage"),
            facilities.get("garden"),
        ),
    )

    cursor.execute(
        """
        INSERT INTO heating_systems (
            building_id,
            fuel_type,
            system_type,
            metering_type,
            allocation_method,
            study_reference
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (building_id) DO UPDATE SET
            fuel_type = EXCLUDED.fuel_type,
            system_type = EXCLUDED.system_type,
            metering_type = EXCLUDED.metering_type,
            allocation_method = EXCLUDED.allocation_method,
            study_reference = EXCLUDED.study_reference,
            updated_at = NOW()
        """,
        (
            building.id,
            heating.get("fuel_type"),
            heating.get("system_type"),
            heating.get("metering_type"),
            heating.get("allocation_method"),
            heating.get("study_reference"),
        ),
    )


def save_building(building, configuration=None):
    """Save a validated building and synchronize relational records."""

    # Financial data always comes from the validated domain object. Optional
    # display names are kept separately in the existing configuration JSONB.
    supplied = configuration
    configuration = json.loads(building_json(building))
    if supplied is not None:
        if load_building(supplied) != building:
            raise ValueError('Η παραμετροποίηση δεν συμφωνεί με την πολυκατοικία.')

    category_ids = list(building.categories)
    rule_ids = [
        _rule_id(building.id, category_id)
        for category_id in category_ids
    ]

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Lock the existing row before merging optional metadata. An
            # ordinary domain save must not silently erase property names.
            cursor.execute(
                "SELECT configuration FROM buildings WHERE id = %s FOR UPDATE",
                (building.id,),
            )
            row = cursor.fetchone()
            existing = row[0] if row else None
            # Archive state is changed only through the dedicated locked action.
            if existing and existing.get('archived', False):
                raise ValueError('Επαναφέρετε την πολυκατοικία πριν αποθηκεύσετε αλλαγές.')
            configuration['property_names'] = property_names_for_save(
                configuration, supplied, existing
            )
            # Preserve the building's explicit expense-category selection.
            # It is UI metadata: never remove financial categories or rules.
            if supplied is not None and SELECTION_KEY in supplied:
                configuration[SELECTION_KEY] = active_category_ids(supplied)
            elif existing is not None and SELECTION_KEY in existing:
                configuration[SELECTION_KEY] = active_category_ids(existing)


            # Recheck removals under the same lock and transaction as the save.
            # A UI preflight alone cannot protect against concurrent changes.
            if existing is not None:
                from property_removal import check_saved_removals
                check_saved_removals(cursor, existing, configuration)

            # The building row serializes concurrent repository saves
            # for the same building.
            cursor.execute(
                """
                INSERT INTO buildings (id, name, address, configuration)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    address = EXCLUDED.address,
                    configuration = EXCLUDED.configuration,
                    updated_at = NOW()
                """,
                (
                    building.id,
                    building.name,
                    building.address,
                    Jsonb(configuration),
                ),
            )

            # Remove obsolete categories before their rules.
            _delete_missing(
                cursor,
                "expense_categories",
                building.id,
                category_ids,
            )

            # Remove old EQUAL participants and allocation shares.
            # They will be recreated from the validated configuration.
            cursor.execute(
                """
                DELETE FROM allocation_rule_participants
                WHERE building_id = %s
                """,
                (building.id,),
            )

            cursor.execute(
                """
                DELETE FROM allocation_shares
                WHERE building_id = %s
                """,
                (building.id,),
            )

            # Remove rules that no longer belong to a category.
            _delete_missing(
                cursor,
                "allocation_rules",
                building.id,
                rule_ids,
            )

            # Insert properties and tables before rules that reference them.
            _sync_apartments(cursor, building)
            _sync_allocation_tables(cursor, building)

            # Update category rules while old tables still exist.
            _sync_rules_and_categories(cursor, building)

            # Remove obsolete tables and properties only after their
            # references have been removed or updated.
            _delete_missing(
                cursor,
                "allocation_tables",
                building.id,
                building.tables.keys(),
            )

            _delete_missing(
                cursor,
                "apartments",
                building.id,
                [apartment.id for apartment in building.apartments],
            )

            _sync_facilities(cursor, building)


def get_building_configuration(building_id):
    """Load the complete JSONB configuration, including optional display names."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT configuration FROM buildings WHERE id = %s",
                (building_id,),
            )
            row = cursor.fetchone()
    return row[0] if row else None


def get_building(building_id):
    """Load a building from PostgreSQL as a domain object."""
    data = get_building_configuration(building_id)
    return load_building(data) if data is not None else None


def list_buildings():
    """Return basic information for the building selector."""

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, name, address
                FROM buildings
                WHERE configuration->>'archived' IS DISTINCT FROM 'true'
                ORDER BY name, id
                """
            )
            rows = cursor.fetchall()

    return [
        {"id": row[0], "name": row[1], "address": row[2]}
        for row in rows
    ]



# Demo lifecycle operations are isolated from configuration persistence.
from building_lifecycle import list_cleanup_candidates, delete_empty_buildings


def delete_empty_test_building(building_id):
    """Backward-compatible single-building cleanup, without name heuristics."""
    return delete_empty_buildings([building_id])
