from django.urls import path
from . import views

urlpatterns = [
    path('api/optimize-route/', views.optimize_route_api, name='optimize_route_api'),
]
