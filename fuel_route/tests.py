from django.test import TestCase, Client
from django.urls import reverse
from unittest.mock import patch
import json

from .optimizer import (
    haversine_distance, 
    SpatialGridIndex, 
    optimize_route,
    _spatial_index
)

class FuelRouteTestCase(TestCase):
    
    def test_haversine_distance(self):
        # Distance from Los Angeles (34.0522, -118.2437) to New York (40.7128, -74.0060)
        # Should be approximately 2445 miles
        dist = haversine_distance(34.0522, -118.2437, 40.7128, -74.0060)
        self.assertAlmostEqual(dist, 2445, delta=50)

    def test_spatial_grid_index(self):
        stations = [
            {'id': '1', 'name': 'Station A', 'Latitude': 34.0, 'Longitude': -118.0, 'price': 3.5},
            {'id': '2', 'name': 'Station B', 'Latitude': 34.1, 'Longitude': -118.1, 'price': 3.0},
            {'id': '3', 'name': 'Station C', 'Latitude': 40.0, 'Longitude': -74.0, 'price': 3.2},
        ]
        index = SpatialGridIndex(stations, cell_size=0.15)
        
        # Query near Station A (34.0, -118.0)
        nearby = index.get_nearby_stations(34.01, -118.01)
        nearby_ids = [s['id'] for s in nearby]
        
        self.assertIn('1', nearby_ids)
        self.assertIn('2', nearby_ids)
        self.assertNotIn('3', nearby_ids) # Station C is in NY, far away

    @patch('fuel_route.optimizer.geocode_address')
    @patch('fuel_route.optimizer.get_route')
    def test_optimize_route_short_trip(self, mock_get_route, mock_geocode):
        # Short trip (e.g. 100 miles) should require 0 stops and $0 cost
        mock_geocode.side_effect = lambda q: {
            'name': q + ' Geocoded',
            'lat': 34.0 if 'Start' in q else 35.0,
            'lon': -118.0 if 'Start' in q else -118.0
        }
        
        # Route geometry with coordinates (roughly 70 miles apart)
        mock_get_route.return_value = {
            'geometry': {
                'type': 'LineString',
                'coordinates': [[-118.0, 34.0], [-118.0, 35.0]]
            },
            'distance_meters': 112654, # ~70 miles
            'duration_seconds': 4000
        }
        
        res = optimize_route("Start City", "End City")
        
        self.assertTrue(res['success'])
        self.assertEqual(res['total_cost'], 0.0)
        self.assertEqual(len(res['stops']), 0)
        self.assertAlmostEqual(res['total_distance'], 69.1, delta=10)

    @patch('fuel_route.optimizer.geocode_address')
    @patch('fuel_route.optimizer.get_route')
    def test_optimize_route_long_trip(self, mock_get_route, mock_geocode):
        # Long trip (e.g. 800 miles) requiring stops.
        # Let's mock geocoding and routing
        mock_geocode.side_effect = lambda q: {
            'name': q + ' Geocoded',
            'lat': 30.0 if 'Start' in q else 38.0,
            'lon': -100.0 if 'Start' in q else -100.0
        }
        
        # Route coordinates sampled at every 100 miles
        # Total distance is 8 degrees of latitude = approx 552 miles
        mock_get_route.return_value = {
            'geometry': {
                'type': 'LineString',
                'coordinates': [
                    [-100.0, 30.0], # 0 miles
                    [-100.0, 32.0], # ~138 miles
                    [-100.0, 34.0], # ~276 miles
                    [-100.0, 36.0], # ~414 miles
                    [-100.0, 38.0]  # ~552 miles
                ]
            },
            'distance_meters': 888000,
            'duration_seconds': 30000
        }
        
        # Mock some fuel stations along the route in the global index
        stations = [
            # Station A at lat 33.0 (approx 207 miles along the route), price $3.00
            {'id': '101', 'name': 'Station A', 'Latitude': 33.0, 'Longitude': -100.0, 'price': 3.00, 'address': '123 A St', 'city': 'A City', 'state': 'CA'},
            # Station B at lat 35.0 (approx 345 miles along the route), price $2.50
            {'id': '102', 'name': 'Station B', 'Latitude': 35.0, 'Longitude': -100.0, 'price': 2.50, 'address': '456 B St', 'city': 'B City', 'state': 'TX'},
            # Station C at lat 37.0 (approx 483 miles along the route), price $3.20
            {'id': '103', 'name': 'Station C', 'Latitude': 37.0, 'Longitude': -100.0, 'price': 3.20, 'address': '789 C St', 'city': 'C City', 'state': 'NY'},
        ]
        
        # Inject custom index
        import fuel_route.optimizer
        fuel_route.optimizer._spatial_index = SpatialGridIndex(stations, cell_size=0.15)
        
        res = optimize_route("Start City", "End City")
        
        self.assertTrue(res['success'])
        # Since it's 552 miles total, we must stop at least once.
        # Stopping at Station A is cheaper ($62.10) than Station B ($86.25) because we buy less fuel
        # at the pumps during the trip by utilizing the initial full tank.
        self.assertEqual(len(res['stops']), 1)
        self.assertEqual(res['stops'][0]['name'], 'Station A')
        self.assertAlmostEqual(res['total_cost'], 62.18, delta=1.0)

    @patch('fuel_route.optimizer.geocode_address')
    @patch('fuel_route.optimizer.get_route')
    def test_unreachable_route(self, mock_get_route, mock_geocode):
        # Trip of 600 miles but no stations available to refuel
        mock_geocode.side_effect = lambda q: {
            'name': q + ' Geocoded',
            'lat': 30.0 if 'Start' in q else 39.0,
            'lon': -100.0 if 'Start' in q else -100.0
        }
        mock_get_route.return_value = {
            'geometry': {
                'type': 'LineString',
                'coordinates': [
                    [-100.0, 30.0],
                    [-100.0, 39.0] # ~620 miles
                ]
            },
            'distance_meters': 1000000,
            'duration_seconds': 40000
        }
        
        # Empty stations list
        import fuel_route.optimizer
        fuel_route.optimizer._spatial_index = SpatialGridIndex([], cell_size=0.15)
        
        res = optimize_route("Start City", "End City")
        self.assertFalse(res['success'])
        self.assertIn("No feasible refueling route found", res['error'])

    # API Endpoint Tests
    def test_api_optimize_invalid_payload(self):
        c = Client()
        response = c.post(
            reverse('optimize_route_api'),
            data='invalid json',
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        res_data = json.loads(response.content)
        self.assertFalse(res_data['success'])
        self.assertEqual(res_data['error'], 'Invalid JSON payload.')

    def test_api_optimize_missing_params(self):
        c = Client()
        response = c.post(
            reverse('optimize_route_api'),
            data=json.dumps({'start': ''}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 400)
        res_data = json.loads(response.content)
        self.assertFalse(res_data['success'])
        self.assertEqual(res_data['error'], 'Both "start" and "finish" parameters are required.')
