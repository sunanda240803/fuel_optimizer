import csv
import math
import urllib.request
import urllib.parse
import json
import os

# Haversine distance formula (in miles)
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 3958.8  # Earth's radius in miles
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# In-Memory Spatial Grid Index for fast lookups
class SpatialGridIndex:
    def __init__(self, stations, cell_size=0.15):
        self.cell_size = cell_size
        self.grid = {}
        for station in stations:
            lat, lon = station['Latitude'], station['Longitude']
            if lat is None or lon is None:
                continue
            cell = (int(lat / cell_size), int(lon / cell_size))
            self.grid.setdefault(cell, []).append(station)

    def get_nearby_stations(self, lat, lon):
        cell_lat = int(lat / self.cell_size)
        cell_lon = int(lon / self.cell_size)
        candidates = []
        # Search the cell and its 8 neighbors
        for d_lat in [-1, 0, 1]:
            for d_lon in [-1, 0, 1]:
                cell = (cell_lat + d_lat, cell_lon + d_lon)
                if cell in self.grid:
                    candidates.extend(self.grid[cell])
        return candidates

# Load fuel prices dataset
def load_fuel_stations(csv_path):
    stations = []
    if not os.path.exists(csv_path):
        return stations
    
    with open(csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lat = float(row['Latitude']) if row.get('Latitude') else None
                lon = float(row['Longitude']) if row.get('Longitude') else None
                price = float(row['Retail Price'])
                stations.append({
                    'id': row['OPIS Truckstop ID'],
                    'name': row['Truckstop Name'],
                    'address': row['Address'],
                    'city': row['City'],
                    'state': row['State'],
                    'price': price,
                    'Latitude': lat,
                    'Longitude': lon
                })
            except (ValueError, KeyError):
                continue
    return stations

# Global storage for stations list and index
_stations_list = []
_spatial_index = None

def initialize_optimizer(csv_path):
    global _stations_list, _spatial_index
    _stations_list = load_fuel_stations(csv_path)
    _spatial_index = SpatialGridIndex(_stations_list)
    print(f"Optimizer initialized with {len(_stations_list)} stations.")

# Geocode location using OpenStreetMap Nominatim API
def geocode_address(address):
    # Search within the US for better accuracy
    query = f"{address}, USA"
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit=1"
    req = urllib.request.Request(url, headers={'User-Agent': 'AntigravityFuelRouteApp/1.0 (hp@gemini.com)'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data:
                return {
                    'name': data[0].get('display_name', address),
                    'lat': float(data[0]['lat']),
                    'lon': float(data[0]['lon'])
                }
    except Exception as e:
        print(f"Geocoding error for '{address}': {e}")
    return None

# Fetch route geometry and distance from OSRM
def get_route(start_lat, start_lon, finish_lat, finish_lon):
    # OSRM expects coordinates as lon,lat;lon,lat
    url = f"http://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{finish_lon},{finish_lat}?overview=full&geometries=geojson"
    req = urllib.request.Request(url, headers={'User-Agent': 'AntigravityFuelRouteApp/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data and data.get('code') == 'Ok':
                route = data['routes'][0]
                return {
                    'geometry': route['geometry'],
                    'distance_meters': route['distance'],
                    'duration_seconds': route['duration']
                }
    except Exception as e:
        print(f"Routing error: {e}")
    return None

# Core route and fuel optimization algorithm
def optimize_route(start_query, finish_query):
    if _spatial_index is None:
        raise ValueError("Optimizer has not been initialized.")

    # 1. Geocode Start & Finish
    start_loc = geocode_address(start_query)
    if not start_loc:
        return {'success': False, 'error': f"Could not geocode start location: {start_query}"}
        
    finish_loc = geocode_address(finish_query)
    if not finish_loc:
        return {'success': False, 'error': f"Could not geocode finish location: {finish_query}"}

    # 2. Get Route from OSRM
    route_data = get_route(start_loc['lat'], start_loc['lon'], finish_loc['lat'], finish_loc['lon'])
    if not route_data:
        return {'success': False, 'error': "Could not calculate route between these locations."}

    geometry = route_data['geometry']
    coords = geometry['coordinates']  # List of [lon, lat]

    # 3. Calculate cumulative distance along the route (in miles) and interpolate sparse segments
    route_points = []
    cum_dist = 0.0
    max_segment_length_miles = 5.0
    
    for i, pt in enumerate(coords):
        lon, lat = pt
        if i > 0:
            prev_lon, prev_lat = coords[i-1]
            segment_dist = haversine_distance(prev_lat, prev_lon, lat, lon)
            
            # If segment between vertices is too large, interpolate intermediate search points
            if segment_dist > max_segment_length_miles:
                num_steps = math.ceil(segment_dist / max_segment_length_miles)
                for step in range(1, num_steps):
                    t = step / num_steps
                    interp_lat = prev_lat + t * (lat - prev_lat)
                    interp_lon = prev_lon + t * (lon - prev_lon)
                    interp_dist = cum_dist + (t * segment_dist)
                    route_points.append({
                        'lat': interp_lat,
                        'lon': interp_lon,
                        'dist_miles': interp_dist
                    })
            
            cum_dist += segment_dist
            
        route_points.append({
            'lat': lat,
            'lon': lon,
            'dist_miles': cum_dist
        })

    total_distance = cum_dist

    # If the trip is less than the max range (500 miles), we don't need any fuel stops!
    if total_distance <= 500.0:
        return {
            'success': True,
            'start': start_loc,
            'finish': finish_loc,
            'total_distance': total_distance,
            'total_cost': 0.0,
            'stops': [],
            'geometry': geometry
        }

    # 4. Find fuel stations along the route (within 10 miles of any route point)
    nearby_stations = {}
    for pt in route_points:
        candidates = _spatial_index.get_nearby_stations(pt['lat'], pt['lon'])
        for station in candidates:
            sid = station['id']
            # Calculate distance from station to this route point
            dist_to_pt = haversine_distance(pt['lat'], pt['lon'], station['Latitude'], station['Longitude'])
            if dist_to_pt <= 10.0:
                if sid not in nearby_stations or dist_to_pt < nearby_stations[sid]['dist_to_route']:
                    nearby_stations[sid] = {
                        'station': station,
                        'dist_to_route': dist_to_pt,
                        'route_dist_miles': pt['dist_miles']
                    }

    # Sort candidates by their position along the route
    sorted_candidates = sorted(nearby_stations.values(), key=lambda x: x['route_dist_miles'])

    # 5. Dynamic Programming to find the optimal path
    # Nodes: 0 (Start), 1..k (Stations), k+1 (Destination)
    nodes = []
    nodes.append({
        'name': 'Start',
        'dist': 0.0,
        'price': 0.0,
        'station': None
    })
    for item in sorted_candidates:
        nodes.append({
            'name': item['station']['name'],
            'dist': item['route_dist_miles'],
            'price': item['station']['price'],
            'station': item['station']
        })
    nodes.append({
        'name': 'Finish',
        'dist': total_distance,
        'price': 0.0,
        'station': None
    })

    n = len(nodes)
    dp = [float('inf')] * n
    parent = [-1] * n

    dp[0] = 0.0

    # DP optimization: find the shortest path in the DAG
    for i in range(1, n):
        for j in range(i):
            dist_diff = nodes[i]['dist'] - nodes[j]['dist']
            # Can we reach i from j on a single full tank (500 miles)?
            if dist_diff <= 500.0:
                if dp[j] != float('inf'):
                    # Cost: if i is finish, we don't purchase fuel. Otherwise, we pay nodes[i]['price']
                    # for the fuel consumed over the distance dist_diff (since we refill to full at i).
                    if i == n - 1:
                        cost = 0.0
                    else:
                        cost = (dist_diff / 10.0) * nodes[i]['price']
                    
                    if dp[j] + cost < dp[i]:
                        dp[i] = dp[j] + cost
                        parent[i] = j

    # If the destination is unreachable, return error
    if dp[n-1] == float('inf'):
        return {
            'success': False,
            'error': "No feasible refueling route found within the vehicle's 500-mile range limits."
        }

    # Reconstruct the path
    stops_indices = []
    curr = parent[n-1]
    while curr > 0:
        stops_indices.append(curr)
        curr = parent[curr]
    stops_indices.reverse()

    # Build the stops details list
    stops_details = []
    for idx in stops_indices:
        node = nodes[idx]
        prev_idx = parent[idx]
        prev_node = nodes[prev_idx]
        
        distance_since_refuel = node['dist'] - prev_node['dist']
        gallons_needed = distance_since_refuel / 10.0
        cost = gallons_needed * node['price']
        
        stops_details.append({
            'name': node['station']['name'],
            'address': node['station']['address'],
            'city': node['station']['city'],
            'state': node['station']['state'],
            'price': node['station']['price'],
            'lat': node['station']['Latitude'],
            'lon': node['station']['Longitude'],
            'dist_along_route': node['dist'],
            'gallons_purchased': gallons_needed,
            'cost': cost
        })

    return {
        'success': True,
        'start': start_loc,
        'finish': finish_loc,
        'total_distance': total_distance,
        'total_cost': dp[n-1],
        'stops': stops_details,
        'geometry': geometry
    }
