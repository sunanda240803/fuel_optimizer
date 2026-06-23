import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .optimizer import optimize_route

@csrf_exempt
@require_POST
def optimize_route_api(request):
    """
    API endpoint that accepts start and finish locations,
    calculates the optimal route and fuel stops, and returns the result.
    """
    try:
        data = json.loads(request.body)
        start = data.get('start', '').strip()
        finish = data.get('finish', '').strip()
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON payload.'}, status=400)

    if not start or not finish:
        return JsonResponse({'success': False, 'error': 'Both "start" and "finish" parameters are required.'}, status=400)

    # Call the optimizer
    result = optimize_route(start, finish)
    
    if not result.get('success', False):
        return JsonResponse(result, status=400)

    return JsonResponse(result)
