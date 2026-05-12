from __future__ import annotations

import logging
from datetime import datetime

from .egov import EgovAdapter
from .base import BidPosting

logger: logging.Logger = logging.getLogger(__name__)


class EminwonAdapter(EgovAdapter):
    """eminwon 시스템(세종시 등) — 기본은 eGov와 유사하므로 상속해서 그대로 사용.

    실제 사이트의 폼/엔드포인트 응답을 확인한 뒤, 필요할 때만 fetch를 오버라이드.
    현재는 표준 어댑터 동작을 우선 적용하고, 차이가 식별되면 이 클래스를 보강.
    """

    def fetch(self, since: datetime) -> list[BidPosting]:
        return super().fetch(since)
