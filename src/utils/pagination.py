from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 10
    page_query_param = "page"
    page_size_query_param = "page_size"
    max_page_size = 1000000


def paginate(view_obj, queryset):
    """
    DRF分页接口封装
    :param view_obj:
    :param queryset:
    :return:
    """
    page = view_obj.paginate_queryset(queryset)
    if page is not None:
        serializer = view_obj.get_serializer(page, many=True)
        result = view_obj.get_paginated_response(serializer.data).data
        result["total"] = result["count"]
        return result
    serializer = view_obj.get_serializer(queryset, many=True)
    result = serializer.data
    result["total"] = result["count"]
    return result
