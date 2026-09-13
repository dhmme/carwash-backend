from .models import AuditLog


class ManagerAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if (
            request.path.startswith('/api/manager/')
            and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
            and user
            and user.is_authenticated
        ):
            forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
            ip_address = forwarded.split(',')[0].strip() or request.META.get('REMOTE_ADDR')
            AuditLog.objects.create(
                user=user,
                method=request.method,
                path=request.path[:255],
                status_code=response.status_code,
                ip_address=ip_address,
            )
        return response
