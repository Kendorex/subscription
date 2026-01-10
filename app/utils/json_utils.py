from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


def _default(o):
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def json_dumps(data, *, ensure_ascii=False, **kwargs) -> str:
    return json.dumps(
        data,
        ensure_ascii=ensure_ascii,
        default=_default,
        **kwargs,
    )
