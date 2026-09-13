from rest_framework.throttling import SimpleRateThrottle


class LoginRateThrottle(SimpleRateThrottle):
    scope = 'login'

    def get_cache_key(self, request, view):
        ip_address = self.get_ident(request)
        username = str(request.data.get('username', '')).strip().lower()
        ident = f'{ip_address}:{username}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class RegisterRateThrottle(SimpleRateThrottle):
    scope = 'register'

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }
