"""Domain exceptions. The CRUD layer raises these; main.py maps them to HTTP responses,
so the persistence layer never depends on FastAPI."""


class AppError(Exception):
    status_code = 400
    code = "app_error"

    def __init__(self, message: str = "Application error") -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class BusinessRuleError(AppError):
    status_code = 422
    code = "business_rule_violation"


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"


class TenantIsolationError(AppError):
    """Raised when code tries to read/write rows outside the session's tenant."""

    status_code = 500
    code = "tenant_isolation_violation"
