class RankingUnavailable(Exception):
    """仅提供固定错误；不携带上游返回正文、地址或数据库异常。"""

    def __init__(self) -> None:
        super().__init__("PyPI rankings are temporarily unavailable")
