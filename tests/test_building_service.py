import unittest
from dataclasses import replace
from unittest.mock import patch

import building_service as service
from configuration import load_building


def sample(building_id='b1'):
    return {
        'id': building_id, 'name': 'Demo', 'address': 'Test address',
        'apartments': [{'id': 'a1', 'code': 'A1'}],
        'tables': [{'id': 'general', 'name': 'General',
                    'weights': {'a1': '1000'}, 'expected_total': '1000'}],
        'categories': [{'id': 'cleaning', 'name': 'Cleaning',
                        'rule': {'type': 'WEIGHTED', 'table_id': 'general'}}],
    }


class BuildingServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        self.get_patch = patch.object(service.repository, 'get_building',
                                      side_effect=self.store.get)
        self.save_patch = patch.object(service.repository, 'save_building',
                                       side_effect=lambda b: self.store.update({b.id: b}))
        self.list_patch = patch.object(service.repository, 'list_buildings',
                                       side_effect=lambda: [dict(id=b.id, name=b.name, address=b.address)
                                                            for b in self.store.values()])
        for patcher in (self.get_patch, self.save_patch, self.list_patch):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_create_and_retrieve(self):
        building = service.create_building(sample())
        self.assertEqual(service.get_building('b1'), building)

    def test_generate_id_without_mutating_input(self):
        data = sample()
        data.pop('id')
        building = service.create_building(data)
        self.assertTrue(building.id)
        self.assertNotIn('id', data)
        self.assertEqual(service.get_building(building.id), building)

    def test_duplicate_create_does_not_overwrite(self):
        original = service.create_building(sample())
        changed = sample()
        changed['name'] = 'Changed'
        with self.assertRaises(service.BuildingAlreadyExists):
            service.create_building(changed)
        self.assertEqual(self.store['b1'], original)

    def test_update(self):
        service.create_building(sample())
        changed = sample()
        changed['name'] = 'Updated'
        self.assertEqual(service.update_building('b1', changed).name, 'Updated')
        self.assertEqual(service.get_building('b1').name, 'Updated')

    def test_update_missing(self):
        with self.assertRaises(service.BuildingNotFound):
            service.update_building('missing', sample('missing'))
        self.assertEqual(self.store, {})

    def test_id_cannot_change(self):
        service.create_building(sample())
        with self.assertRaises(service.BuildingServiceError):
            service.update_building('b1', sample('other'))
        self.assertEqual(service.get_building('b1').id, 'b1')

    def test_invalid_configuration_is_not_saved(self):
        data = sample()
        data['tables'][0]['weights']['a1'] = '-1'
        with self.assertRaises(ValueError):
            service.create_building(data)
        self.assertEqual(self.store, {})

    def test_existing_domain_object(self):
        building = load_building(sample())
        self.assertEqual(service.create_building(building), building)
        self.assertEqual(service.update_building('b1', replace(building, name='New')).name, 'New')

    def test_list_and_missing(self):
        self.assertEqual(service.list_buildings(), [])
        with self.assertRaises(service.BuildingNotFound):
            service.get_building('missing')
        service.create_building(sample())
        self.assertEqual(service.list_buildings()[0]['id'], 'b1')


if __name__ == '__main__':
    unittest.main()
