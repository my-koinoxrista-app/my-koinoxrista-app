"""Test saving and loading a building from PostgreSQL."""

import json
from pathlib import Path

from building_repository import save_building, get_building, list_buildings
from configuration import load_building


def main():
    demo_path = Path(__file__).parent / "examples" / "building_demo.json"

    with demo_path.open(encoding="utf-8") as file:
        data = json.load(file)

    # Use a separate ID so we do not overwrite another building.
    data["id"] = "test-building-001"
    data["name"] = "TEST - Demo Building"

    building = load_building(data)

    print("Saving building...")
    save_building(building)

    print("Loading building...")
    loaded = get_building(building.id)

    assert loaded is not None
    assert loaded.id == building.id
    assert loaded.name == building.name
    assert len(loaded.apartments) == len(building.apartments)
    assert set(loaded.tables) == set(building.tables)

    print("Buildings in database:")
    for item in list_buildings():
        print(item)

    print("Persistence test passed.")


if __name__ == "__main__":
    main()