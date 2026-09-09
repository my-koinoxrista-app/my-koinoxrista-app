"""Integration test for relational building persistence."""

from decimal import Decimal
from uuid import uuid4

from building_service import create_building, update_building
from configuration import building_json
from database import get_connection

import json


def fetch_relational_data(building_id):
    """Read apartments, allocation tables and shares from PostgreSQL."""

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, code, property_type, floor, area_sqm
                FROM apartments
                WHERE building_id = %s
                ORDER BY id
                """,
                (building_id,),
            )
            apartments = cursor.fetchall()

            cursor.execute(
                """
                SELECT id, name, expected_total, source_reference
                FROM allocation_tables
                WHERE building_id = %s
                ORDER BY id
                """,
                (building_id,),
            )
            tables = cursor.fetchall()

            cursor.execute(
                """
                SELECT allocation_table_id, apartment_id, weight
                FROM allocation_shares
                WHERE building_id = %s
                ORDER BY allocation_table_id, apartment_id
                """,
                (building_id,),
            )
            shares = cursor.fetchall()

    return apartments, tables, shares


def main():
    building_id = f"relational-test-{uuid4()}"

    data = {
        "id": building_id,
        "name": "TEST - Relational Building",
        "address": "Εικονική διεύθυνση",
        "apartments": [
            {
                "id": f"{building_id}-a1",
                "code": "A1",
                "property_type": "APARTMENT",
                "floor": 1,
                "area_sqm": "80.50",
            },
            {
                "id": f"{building_id}-a2",
                "code": "A2",
                "property_type": "APARTMENT",
                "floor": 2,
                "area_sqm": "95.00",
            },
        ],
        "tables": [
            {
                "id": f"{building_id}-general",
                "name": "Γενικά χιλιοστά",
                "expected_total": "1000",
                "source_reference": "TEST - Εικονικός πίνακας",
                "weights": {
                    f"{building_id}-a1": "450",
                    f"{building_id}-a2": "550",
                },
            }
        ],
        "categories": [],
        "facilities": {},
        "heating": {},
    }

    print("1. Creating building...")
    created = create_building(data)

    apartments, tables, shares = fetch_relational_data(building_id)

    assert apartments == [
        (f"{building_id}-a1", "A1", "APARTMENT", 1, Decimal("80.50")),
        (f"{building_id}-a2", "A2", "APARTMENT", 2, Decimal("95.00")),
    ]

    assert tables == [
        (
            f"{building_id}-general",
            "Γενικά χιλιοστά",
            Decimal("1000"),
            "TEST - Εικονικός πίνακας",
        )
    ]

    assert shares == [
        (f"{building_id}-general", f"{building_id}-a1", Decimal("450")),
        (f"{building_id}-general", f"{building_id}-a2", Decimal("550")),
    ]

    print("2. Updating apartment and allocation weights...")

    updated_data = json.loads(building_json(created))
    updated_data["apartments"][0]["area_sqm"] = "82.00"
    updated_data["tables"][0]["weights"][f"{building_id}-a1"] = "400"
    updated_data["tables"][0]["weights"][f"{building_id}-a2"] = "600"

    update_building(building_id, updated_data)

    apartments, tables, shares = fetch_relational_data(building_id)

    assert apartments[0][4] == Decimal("82.00")

    assert shares == [
        (f"{building_id}-general", f"{building_id}-a1", Decimal("400")),
        (f"{building_id}-general", f"{building_id}-a2", Decimal("600")),
    ]

    print()
    print("Relational persistence test passed.")
    print(f"Test building ID: {building_id}")


if __name__ == "__main__":
    main()