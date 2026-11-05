from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.http import require_GET, require_http_methods

from .warehouse import Warehouse


@require_GET
def item(request: HttpRequest, item: int) -> JsonResponse:
    return JsonResponse({"item": item})


class Reservations(View):
    def get(self, request: HttpRequest) -> JsonResponse:
        return JsonResponse([], safe=False)

    def post(self, request: HttpRequest) -> JsonResponse:
        Warehouse().reserve(request.POST["sku"])
        return JsonResponse({}, status=201)


@require_http_methods(["GET", "DELETE"])
def file(request: HttpRequest, name: str) -> HttpResponse:
    return HttpResponse(name)


def legacy(request: HttpRequest, sku: str) -> HttpResponse:
    return HttpResponse(sku)
