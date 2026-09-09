"""Integration test for the Building Service and PostgreSQL."""

import json
from pathlib import Path
from uuid import uuid4

from building_service import (
    BuildingAlreadyExists,
    create_building,
    get_building,
    list_buildings,
    update_building,
)
from configuration import building_json


def main():
    demo_path = Path(__file__).parent / "examples" / "building_demo.json"

    with demo_path.open(encoding="utf-8") as file:
        data = json.load(file)

    # A unique ID prevents overwriting an existing building.
    building_id = f"integration-test-{uuid4()}"

    data["id"] = building_id
    data["name"] = "TEST - Integration Building"
    data["address"] = "Εικονική διεύθυνση"

    print("1. Creating building...")
    created = create_building(data)

    assert created.id == building_id
    assert created.name == "TEST - Integration Building"

    print("2. Loading building...")
    loaded = get_building(building_id)

    assert loaded == created

    print("3. Checking duplicate creation...")
    try:
        create_building(data)
    except BuildingAlreadyExists:
        print("Duplicate creation correctly rejected.")
    else:
        raise AssertionError("Duplicate creation was not rejected.")

    print("4. Updating building...")
    updated_data = json.loads(building_json(loaded))
    updated_data["name"] = "TEST - Updated Building"
    updated_data["address"] = "Νέα εικονική διεύθυνση"

    updated = update_building(building_id, updated_data)

    assert updated.id == building_id
    assert updated.name == "TEST - Updated Building"

    print("5. Loading updated building...")
    reloaded = get_building(building_id)

    assert reloaded == updated
    assert reloaded.address == "Νέα εικονική διεύθυνση"

    print("6. Checking building list...")
    buildings = list_buildings()

    assert any(item["id"] == building_id for item in buildings)

    print()
    print("Integration test passed.")
    print(f"Test building ID: {building_id}")


if __name__ == "__main__":
    main()