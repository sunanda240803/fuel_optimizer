import os
from django.apps import AppConfig
from django.conf import settings

class FuelRouteConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fuel_route'

    def ready(self):
        # Load the spatial index on server startup.
        # If in debug mode, prevent running twice on auto-reload.
        if os.environ.get('RUN_MAIN') == 'true' or not settings.DEBUG or 'test' in os.sys.argv:
            from .optimizer import initialize_optimizer
            csv_path = os.path.join(os.path.dirname(__file__), 'data', 'fuel_prices_geocoded.csv')
            initialize_optimizer(csv_path)
