"""Shared API error type.

Lives in its own module so any layer (HA client, security, routes) can raise it
without importing app.py - which would create an import cycle.
"""


class ApiError(Exception):
    def __init__(self, message, status=500, extra=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra or {}  # extra JSON fields (e.g. {"expired": True})
