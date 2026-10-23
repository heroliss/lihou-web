"""Signed expiring tokens using current itsdangerous (legacy JWTs are not accepted)."""
import time
from itsdangerous import URLSafeTimedSerializer, SignatureExpired


class TokenSerializer:
    def __init__(self, secret_key, expires_in=3600):
        self.serializer = URLSafeTimedSerializer(secret_key, salt='lihou-web-v2')
        self.expires_in = expires_in

    def dumps(self, payload):
        return self.serializer.dumps(dict(payload, expires_at=time.time() + self.expires_in))

    def loads(self, token):
        data = self.serializer.loads(token)
        if data.get('expires_at', 0) < time.time():
            raise SignatureExpired('Token expired')
        return data
