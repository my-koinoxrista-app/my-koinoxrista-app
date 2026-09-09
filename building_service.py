"""Application operations for building configurations, independent of the UI."""

from uuid import uuid4

from configuration import load_building
from expense_engine import Building, validate_building
import building_repository as repository


class BuildingServiceError(ValueError):
    """A requested building operation cannot be completed."""


class BuildingNotFound(BuildingServiceError):
    pass


class BuildingAlreadyExists(BuildingServiceError):
    pass


def _building(value):
    """Accept a Building or a configuration dictionary and validate it."""
    if isinstance(value, Building):
        validate_building(value)
        return value
    if isinstance(value, dict):
        return load_building(value)
    raise BuildingServiceError('Expected a Building or configuration dictionary.')


def create_building(data):
    """Create a validated building; generate its ID when it is missing."""
    if isinstance(data, dict):
        data = dict(data)
        if not data.get('id'):
            data['id'] = str(uuid4())
    building = _building(data)
    if repository.get_building(building.id) is not None:
        raise BuildingAlreadyExists(f'Building {building.id} already exists.')
    repository.save_building(building, configuration=data if isinstance(data, dict) else None)
    return building


def update_building(building_id, data):
    """Replace an existing configuration, retaining its permanent ID."""
    current = repository.get_building(building_id)
    if current is None:
        raise BuildingNotFound(f'Building {building_id} was not found.')
    building = _building(data)
    if building.id != building_id:
        raise BuildingServiceError('A building ID cannot be changed.')
    repository.save_building(building, configuration=data if isinstance(data, dict) else None)
    return building


def get_building(building_id):
    """Return an existing building or raise a clear application error."""
    building = repository.get_building(building_id)
    if building is None:
        raise BuildingNotFound(f'Building {building_id} was not found.')
    return building


def get_building_configuration(building_id):
    """Return the persisted configuration, including optional property names."""
    data = repository.get_building_configuration(building_id)
    if data is None:
        raise BuildingNotFound(f'Building {building_id} was not found.')
    load_building(data)
    return data


def list_buildings():
    """Return the available buildings for a future UI selector."""
    return repository.list_buildings()




def create_test_building(name, address=''):
    """Create an explicitly marked empty demo building, without invented weights."""
    name = str(name).strip()
    if not name:
        raise BuildingServiceError('Συμπλήρωσε όνομα πολυκατοικίας.')
    return create_building({
        'id': 'test-' + str(uuid4()),
        'name': name,
        'address': str(address).strip(),
        'apartments': [{'id': 'a1', 'code': 'Α1', 'property_type': 'APARTMENT',
                        'floor': None, 'area_sqm': None}],
        'tables': [], 'categories': [], 'facilities': {}, 'heating': {},
    })


def delete_test_building(building_id):
    """Never delete a regular building or one with dependent historical data."""
    repository.delete_empty_test_building(building_id)


def list_cleanup_candidates():
    """Inspect which buildings have historical or unknown dependencies."""
    return repository.list_cleanup_candidates()


def delete_empty_buildings(building_ids):
    """Delete an explicitly selected batch, atomically and only if empty."""
    return repository.delete_empty_buildings(building_ids)
