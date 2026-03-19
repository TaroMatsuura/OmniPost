from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any


TICKET_TYPE_ALIASES = {
    "TAN": "tan",
    "WIN": "tan",
    "TANSHO": "tan",
    "FUKU": "fuku",
    "PLACE": "fuku",
    "FUKUSHO": "fuku",
    "WAKU": "waku",
    "WIDE": "wide",
    "REN": "ren",
    "QUINELLA": "ren",
    "UMATAN": "umatan",
    "EXACTA": "umatan",
    "SANPUKU": "sanpuku",
    "TRIO": "sanpuku",
    "SANTAN": "santan",
    "TRIFECTA": "santan",
    "BRACKET_QUINELLA": "waku",
    "WIN5": "win5",
    "単勝": "tan",
    "複勝": "fuku",
    "枠連": "waku",
    "ワイド": "wide",
    "馬連": "ren",
    "馬単": "umatan",
    "三連複": "sanpuku",
    "3連複": "sanpuku",
    "三連単": "santan",
    "3連単": "santan",
}

ALLOWED_TICKET_TYPES = {
    "tan",
    "fuku",
    "wide",
    "ren",
    "umatan",
    "sanpuku",
    "santan",
    "waku",
    "win5",
}


class OrderValidationError(ValueError):
    pass


@dataclass(slots=True)
class Win5Details:
    select_n1: list[int]
    select_n2: list[int]
    select_n3: list[int]
    select_n4: list[int]
    select_n5: list[int]
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_combinations(self) -> int:
        total = 1
        for selections in (
            self.select_n1,
            self.select_n2,
            self.select_n3,
            self.select_n4,
            self.select_n5,
        ):
            total *= len(selections)
        return total


@dataclass(slots=True)
class BetOrder:
    order_id: str
    race_id: str
    post_time: time
    ticket_type: str
    amount: int
    horse_number: int | None = None
    unit_amount: int | None = None
    total_combinations: int | None = None
    win5_details: Win5Details | None = None
    min_odds: float | None = None
    expected_ev: float | None = None
    memo: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrderRequest:
    version: str
    sender: str
    request_id: str
    timestamp: datetime
    orders: list[BetOrder]
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_amount(self) -> int:
        return sum(order.amount for order in self.orders)

    @property
    def race_ids(self) -> list[str]:
        return sorted({order.race_id for order in self.orders})


def parse_order_request(payload: dict[str, Any]) -> OrderRequest:
    if not isinstance(payload, dict):
        raise OrderValidationError("トップレベル JSON はオブジェクトである必要があります")

    version = _require_non_empty_string(payload, "version")
    sender = _require_non_empty_string(payload, "sender")
    request_id = _require_non_empty_string(payload, "request_id")
    timestamp = _parse_timestamp(payload.get("timestamp"))

    orders_payload = payload.get("orders")
    if not isinstance(orders_payload, list) or not orders_payload:
        raise OrderValidationError("orders は 1 件以上の配列である必要があります")

    orders = [_parse_bet_order(item, index) for index, item in enumerate(orders_payload, start=1)]
    return OrderRequest(
        version=version,
        sender=sender,
        request_id=request_id,
        timestamp=timestamp,
        orders=orders,
        raw=payload,
    )


def _parse_bet_order(item: Any, index: int) -> BetOrder:
    if not isinstance(item, dict):
        raise OrderValidationError(f"orders[{index}] はオブジェクトである必要があります")

    order_id = _require_non_empty_string(item, "order_id", f"orders[{index}].order_id")
    race_id = _parse_race_id(item.get("race_id"), index)
    post_time = _parse_post_time(item.get("post_time"), index)
    ticket_type = _normalize_ticket_type(item.get("ticket_type"), index)
    amount = _parse_amount(item.get("amount"), index)
    min_odds = _parse_optional_float(item.get("min_odds"), index, "min_odds")
    expected_ev = _parse_optional_float(item.get("expected_ev"), index, "expected_ev")
    memo = _parse_optional_string(item.get("memo"), index, "memo")

    horse_number: int | None = None
    unit_amount: int | None = None
    total_combinations: int | None = None
    win5_details: Win5Details | None = None

    if ticket_type == "win5":
        win5_details = _parse_win5_details(item.get("win5_details"), index)
        unit_amount = _parse_amount(item.get("unit_amount"), index, field_name="unit_amount")
        total_combinations = _parse_positive_int(item.get("total_combinations"), index, "total_combinations")
        if total_combinations != win5_details.total_combinations:
            raise OrderValidationError(
                f"orders[{index}].total_combinations が win5_details と一致しません"
            )
        if amount != unit_amount * total_combinations:
            raise OrderValidationError(
                f"orders[{index}].amount は unit_amount * total_combinations と一致する必要があります"
            )
    else:
        horse_number = _parse_horse_number(item.get("horse_no", item.get("horse_number")), index)

    return BetOrder(
        order_id=order_id,
        race_id=race_id,
        post_time=post_time,
        ticket_type=ticket_type,
        amount=amount,
        horse_number=horse_number,
        unit_amount=unit_amount,
        total_combinations=total_combinations,
        win5_details=win5_details,
        min_odds=min_odds,
        expected_ev=expected_ev,
        memo=memo,
        raw=item,
    )


def _require_non_empty_string(
    payload: dict[str, Any],
    field_name: str,
    error_label: str | None = None,
) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise OrderValidationError(f"{error_label or field_name} は空でない文字列である必要があります")
    return value.strip()


def _parse_horse_number(value: Any, index: int) -> int:
    try:
        horse_number = int(value)
    except (TypeError, ValueError):
        raise OrderValidationError(f"orders[{index}].horse_no は整数である必要があります") from None

    if horse_number < 1 or horse_number > 18:
        raise OrderValidationError(f"orders[{index}].horse_no は 1 から 18 の範囲で指定してください")
    return horse_number


def _normalize_ticket_type(value: Any, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrderValidationError(f"orders[{index}].ticket_type は空でない文字列である必要があります")

    candidate = value.strip()
    normalized = TICKET_TYPE_ALIASES.get(candidate.upper(), TICKET_TYPE_ALIASES.get(candidate, candidate.lower()))
    if normalized not in ALLOWED_TICKET_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TICKET_TYPES))
        raise OrderValidationError(
            f"orders[{index}].ticket_type は対応券種である必要があります: {allowed}"
        )
    return normalized


def _parse_amount(value: Any, index: int, field_name: str = "amount") -> int:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise OrderValidationError(f"orders[{index}].{field_name} は数値である必要があります") from None

    if decimal_value != decimal_value.to_integral_value():
        raise OrderValidationError(f"orders[{index}].{field_name} は整数である必要があります")

    amount = int(decimal_value)
    if amount <= 0:
        raise OrderValidationError(f"orders[{index}].{field_name} は 1 以上である必要があります")
    if amount % 100 != 0:
        raise OrderValidationError(f"orders[{index}].{field_name} は 100 円単位で指定してください")
    return amount


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OrderValidationError("timestamp は空でない ISO 8601 文字列である必要があります")

    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        raise OrderValidationError("timestamp は ISO 8601 形式で指定してください") from None


def _parse_race_id(value: Any, index: int) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) != 14:
        raise OrderValidationError(f"orders[{index}].race_id は 14 桁の文字列である必要があります")
    return value


def _parse_post_time(value: Any, index: int) -> time:
    if not isinstance(value, str) or not value.strip():
        raise OrderValidationError(f"orders[{index}].post_time は HH:MM 文字列である必要があります")
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise OrderValidationError(f"orders[{index}].post_time は HH:MM 形式で指定してください") from None


def _parse_positive_int(value: Any, index: int, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise OrderValidationError(f"orders[{index}].{field_name} は整数である必要があります") from None
    if parsed <= 0:
        raise OrderValidationError(f"orders[{index}].{field_name} は 1 以上である必要があります")
    return parsed


def _parse_optional_float(value: Any, index: int, field_name: str) -> float | None:
    if value is None:
        return None

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise OrderValidationError(f"orders[{index}].{field_name} は数値である必要があります") from None

    if parsed <= 0:
        raise OrderValidationError(f"orders[{index}].{field_name} は 0 より大きい必要があります")
    return parsed


def _parse_optional_string(value: Any, index: int, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise OrderValidationError(f"orders[{index}].{field_name} は文字列である必要があります")
    stripped = value.strip()
    return stripped or None


def _parse_win5_details(value: Any, index: int) -> Win5Details:
    if not isinstance(value, dict):
        raise OrderValidationError(f"orders[{index}].win5_details はオブジェクトである必要があります")

    selections: dict[str, list[int]] = {}
    for leg in range(1, 6):
        field_name = f"select_n{leg}"
        raw_selection = value.get(field_name)
        if not isinstance(raw_selection, list) or not raw_selection:
            raise OrderValidationError(f"orders[{index}].win5_details.{field_name} は 1 件以上の配列である必要があります")
        selections[field_name] = [_parse_horse_number(candidate, index) for candidate in raw_selection]

    return Win5Details(
        select_n1=selections["select_n1"],
        select_n2=selections["select_n2"],
        select_n3=selections["select_n3"],
        select_n4=selections["select_n4"],
        select_n5=selections["select_n5"],
        raw=value,
    )