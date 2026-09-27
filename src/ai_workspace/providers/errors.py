class ProviderError(Exception):
    """A provider request failed without exposing upstream data or credentials."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
