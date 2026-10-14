class ProviderError(Exception):
    """供应商请求失败，异常中不暴露上游数据或凭据。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
