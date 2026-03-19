from __future__ import annotations

import logging
import math
import time as time_module
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ipat_vote_driver import IPATVoteDriver
from ipat_win5_vote_driver import IPATWin5VoteDriver

from .config import OmniPostConfig
from .models import BetOrder, OrderRequest

logger = logging.getLogger(__name__)

JYO_CODE_MAP = {
    "01": "札幌",
    "02": "福島",
    "03": "函館",
    "04": "新潟",
    "05": "東京",
    "06": "中山",
    "07": "中京",
    "08": "京都",
    "09": "阪神",
    "10": "小倉",
}

NORMAL_BET_TYPE_MAP = {
    "tan": "tansho",
    "fuku": "fukusho",
    "ren": "umaren",
    "umatan": "umatan",
    "sanpuku": "sanrenpuku",
    "santan": "sanrentan",
}


@dataclass(slots=True)
class OrderExecutionResult:
    order_id: str
    status: str
    message: str
    amount: int
    race_id: str
    ticket_type: str


@dataclass(slots=True)
class ExecutionSummary:
    status: str
    message: str
    executed_amount: int = 0
    skipped_amount: int = 0
    failed_amount: int = 0
    purchase_limit_before: int | None = None
    purchase_limit_after: int | None = None
    order_results: list[OrderExecutionResult] = field(default_factory=list)


@dataclass(slots=True)
class ExecutionBatch:
    race_id: str
    ticket_type: str
    post_time: datetime.time
    orders: list[BetOrder]

    @property
    def batch_id(self) -> str:
        return f"{self.race_id}:{self.ticket_type}"

    @property
    def race_number(self) -> int:
        return int(self.race_id[-2:])

    @property
    def requested_amount(self) -> int:
        return sum(order.amount for order in self.orders)

    @property
    def is_win5(self) -> bool:
        return self.ticket_type == "win5"


class IPATExecutionService:
    def __init__(self, config: OmniPostConfig):
        self.config = config

    def execute_request(self, request: OrderRequest) -> ExecutionSummary:
        if not self.config.execute_votes:
            return ExecutionSummary(
                status="disabled",
                message="execution disabled by OMNIPOST_EXECUTE_VOTES",
                skipped_amount=request.total_amount,
                order_results=[
                    OrderExecutionResult(
                        order_id=order.order_id,
                        status="skipped",
                        message="execution disabled",
                        amount=order.amount,
                        race_id=order.race_id,
                        ticket_type=order.ticket_type,
                    )
                    for order in request.orders
                ],
            )

        batches = self._build_execution_batches(request)
        if self.config.simulate_ipat:
            return self._simulate_request(request, batches)
        return self._execute_scheduled_request(request, batches)

    def _simulate_request(self, request: OrderRequest, batches: list[ExecutionBatch]) -> ExecutionSummary:
        order_results: list[OrderExecutionResult] = []
        executed_amount = 0
        skipped_amount = 0
        failed_amount = 0
        purchase_limit = self.config.simulation_purchase_limit
        remaining_limit = purchase_limit

        cohorts = self._group_batches_into_cohorts(batches)
        is_first_cohort = True
        for cohort in cohorts:
            cohort_plan, bankroll_mode = self._plan_cohort_amounts(
                cohort,
                remaining_limit,
                use_json_amounts=is_first_cohort,
            )
            cohort_executed = 0
            cohort_requested = sum(batch.requested_amount for batch in cohort)

            for batch in cohort:
                unsupported_message = self._unsupported_normal_bet_message(batch.ticket_type) if not batch.is_win5 else None
                if unsupported_message:
                    for order in batch.orders:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=f"simulation: {unsupported_message}",
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    continue

                blocking_reason = self._cutoff_reason(batch.orders[0], request.timestamp)
                if blocking_reason:
                    for order in batch.orders:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=f"simulation: {blocking_reason}",
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    continue

                batch_amounts = cohort_plan.get(batch.batch_id, {})
                effective_total = sum(batch_amounts.values())
                if effective_total <= 0:
                    for order in batch.orders:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=f"simulation: purchase limit shortage: required={cohort_requested} available={remaining_limit}",
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    continue

                cohort_executed += effective_total
                try:
                    jyo_name, race_num = self._parse_race_id(batch.race_id)
                    base_message = f"simulation: would submit {len(batch.orders)} order(s) for {jyo_name}{race_num}R"
                except Exception:
                    base_message = f"simulation: would submit {len(batch.orders)} order(s)"
                if batch.is_win5:
                    base_message = "simulation: would submit win5 order"
                message = f"{base_message} mode={bankroll_mode} total={effective_total}"
                for order in batch.orders:
                    order_results.append(
                        OrderExecutionResult(
                            order_id=order.order_id,
                            status="executed",
                            message=message,
                            amount=batch_amounts.get(order.order_id, 0),
                            race_id=order.race_id,
                            ticket_type=order.ticket_type,
                        )
                    )

            remaining_limit = max(0, remaining_limit - cohort_executed)
            executed_amount += cohort_executed
            is_first_cohort = False

        status = "executed"
        if failed_amount and executed_amount:
            status = "partial"
        elif failed_amount and not executed_amount:
            status = "failed"
        elif skipped_amount and not executed_amount:
            status = "skipped"
        elif skipped_amount:
            status = "partial"

        return ExecutionSummary(
            status=status,
            message=(
                f"simulation={status} executed={executed_amount} skipped={skipped_amount} failed={failed_amount}"
            ),
            executed_amount=executed_amount,
            skipped_amount=skipped_amount,
            failed_amount=failed_amount,
            purchase_limit_before=purchase_limit,
            purchase_limit_after=remaining_limit,
            order_results=order_results,
        )

    def _execute_scheduled_request(self, request: OrderRequest, batches: list[ExecutionBatch]) -> ExecutionSummary:
        order_results: list[OrderExecutionResult] = []
        executed_amount = 0
        skipped_amount = 0
        failed_amount = 0
        purchase_limit_before: int | None = None
        purchase_limit_after: int | None = None
        cohorts = self._group_batches_into_cohorts(batches)
        is_first_cohort = True

        for cohort in cohorts:
            self._wait_until_preparation_window(cohort, request.timestamp)
            current_limit = self._peek_purchase_limit()
            if purchase_limit_before is None:
                purchase_limit_before = current_limit
            purchase_limit_after = current_limit
            cohort_plan, bankroll_mode = self._plan_cohort_amounts(
                cohort,
                current_limit,
                use_json_amounts=is_first_cohort,
            )

            for batch in cohort:
                batch_amounts = cohort_plan.get(batch.batch_id, {})
                subrequest = self._subset_request(request, batch.orders)
                if batch.is_win5:
                    summary = self._execute_win5_orders(
                        subrequest,
                        batch.orders,
                        resolved_amounts=batch_amounts,
                        bankroll_mode=bankroll_mode,
                    )
                else:
                    summary = self._execute_normal_orders(
                        subrequest,
                        batch.orders,
                        resolved_amounts=batch_amounts,
                        bankroll_mode=bankroll_mode,
                    )
                order_results.extend(summary.order_results)
                executed_amount += summary.executed_amount
                skipped_amount += summary.skipped_amount
                failed_amount += summary.failed_amount
                purchase_limit_after = summary.purchase_limit_after or purchase_limit_after

            is_first_cohort = False

        status = "executed"
        if failed_amount and executed_amount:
            status = "partial"
        elif failed_amount and not executed_amount:
            status = "failed"
        elif skipped_amount and not executed_amount:
            status = "skipped"
        elif skipped_amount:
            status = "partial"

        return ExecutionSummary(
            status=status,
            message=(
                f"execution={status} executed={executed_amount} skipped={skipped_amount} failed={failed_amount}"
            ),
            executed_amount=executed_amount,
            skipped_amount=skipped_amount,
            failed_amount=failed_amount,
            purchase_limit_before=purchase_limit_before,
            purchase_limit_after=purchase_limit_after,
            order_results=order_results,
        )

    def _execute_normal_orders(
        self,
        request: OrderRequest,
        orders: list[BetOrder],
        resolved_amounts: dict[str, int] | None = None,
        bankroll_mode: str | None = None,
    ) -> ExecutionSummary:
        grouped: dict[tuple[str, str], list[BetOrder]] = defaultdict(list)
        for order in sorted(orders, key=lambda item: (item.post_time, item.race_id, item.order_id)):
            grouped[(order.race_id, order.ticket_type)].append(order)

        order_results: list[OrderExecutionResult] = []
        executed_amount = 0
        skipped_amount = 0
        failed_amount = 0
        purchase_limit_before: int | None = None
        purchase_limit_after: int | None = None

        driver: IPATVoteDriver | None = None
        try:
            driver = IPATVoteDriver()
            driver.start()
            driver.login()
            driver.select_normal_bet()

            for (race_id, ticket_type), group_orders in grouped.items():
                unsupported_message = self._unsupported_normal_bet_message(ticket_type)
                if unsupported_message:
                    for order in group_orders:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=unsupported_message,
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    continue

                blocking_reason = self._cutoff_reason(group_orders[0], request.timestamp)
                if blocking_reason:
                    for order in group_orders:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=blocking_reason,
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    continue

                group_total = sum(order.amount for order in group_orders)
                purchase_limit = driver.get_purchase_limit()
                if purchase_limit_before is None:
                    purchase_limit_before = purchase_limit
                purchase_limit_after = purchase_limit
                active_mode = bankroll_mode or "fixed"
                if resolved_amounts is None:
                    effective_amounts, active_mode = self._resolve_effective_amounts(group_orders, purchase_limit)
                else:
                    effective_amounts = {order.order_id: resolved_amounts.get(order.order_id, 0) for order in group_orders}
                effective_group_total = sum(effective_amounts.values())
                if purchase_limit is not None and effective_group_total <= 0:
                    message = f"purchase limit shortage: required={group_total} available={purchase_limit}"
                    for order in group_orders:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=message,
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    continue
                if purchase_limit is not None and active_mode == "fixed" and purchase_limit < group_total:
                    message = f"purchase limit shortage: required={group_total} available={purchase_limit}"
                    for order in group_orders:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=message,
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    continue

                try:
                    jyo_name, race_num = self._parse_race_id(group_orders[0].race_id)
                    driver.select_course_and_race(jyo_name, race_num, expected_time=group_orders[0].post_time.strftime("%H:%M"))
                    horse_amount_list = [
                        (order.horse_number, effective_amounts[order.order_id])
                        for order in group_orders
                        if order.horse_number is not None
                        and effective_amounts.get(order.order_id, 0) > 0
                    ]
                    success = driver.vote_horses(
                        horse_amount_list,
                        bet_type=NORMAL_BET_TYPE_MAP[ticket_type],
                        formation="SINGLE",
                        finalize=True,
                        clear_cart=True,
                        calculated_total=effective_group_total,
                    )
                    if success:
                        executed_amount += effective_group_total
                        message = (
                            f"submitted {len(horse_amount_list)} order(s) for {jyo_name}{race_num}R "
                            f"mode={active_mode} total={effective_group_total}"
                        )
                        for order in group_orders:
                            order_results.append(
                                OrderExecutionResult(
                                    order_id=order.order_id,
                                    status="executed",
                                    message=message,
                                    amount=effective_amounts.get(order.order_id, 0),
                                    race_id=order.race_id,
                                    ticket_type=order.ticket_type,
                                )
                            )
                    else:
                        failed_amount += effective_group_total
                        for order in group_orders:
                            order_results.append(
                                OrderExecutionResult(
                                    order_id=order.order_id,
                                    status="failed",
                                    message="driver returned False",
                                    amount=effective_amounts.get(order.order_id, 0),
                                    race_id=order.race_id,
                                    ticket_type=order.ticket_type,
                                )
                            )
                    driver.handle_continue_voting()
                    refreshed_limit = driver.get_purchase_limit()
                    if refreshed_limit is not None:
                        purchase_limit_after = refreshed_limit
                except Exception as exc:
                    logger.exception("Failed normal bet execution for race=%s ticket_type=%s", race_id, ticket_type)
                    failed_amount += effective_group_total
                    for order in group_orders:
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="failed",
                                message=str(exc),
                                amount=effective_amounts.get(order.order_id, 0),
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
        except Exception as exc:
            logger.exception("Failed to initialize normal bet driver")
            for order in orders:
                failed_amount += order.amount
                order_results.append(
                    OrderExecutionResult(
                        order_id=order.order_id,
                        status="failed",
                        message=str(exc),
                        amount=order.amount,
                        race_id=order.race_id,
                        ticket_type=order.ticket_type,
                    )
                )
        finally:
            if driver is not None:
                driver.close()

        return ExecutionSummary(
            status="done",
            message="normal bet execution finished",
            executed_amount=executed_amount,
            skipped_amount=skipped_amount,
            failed_amount=failed_amount,
            purchase_limit_before=purchase_limit_before,
            purchase_limit_after=purchase_limit_after,
            order_results=order_results,
        )

    def _execute_win5_orders(
        self,
        request: OrderRequest,
        orders: list[BetOrder],
        resolved_amounts: dict[str, int] | None = None,
        bankroll_mode: str | None = None,
    ) -> ExecutionSummary:
        order_results: list[OrderExecutionResult] = []
        executed_amount = 0
        skipped_amount = 0
        failed_amount = 0
        purchase_limit_before: int | None = None
        purchase_limit_after: int | None = None

        driver: IPATWin5VoteDriver | None = None
        try:
            driver = IPATWin5VoteDriver()
            driver.start()
            driver.login()

            for order in sorted(orders, key=lambda item: (item.post_time, item.order_id)):
                if order.win5_details is None:
                    failed_amount += order.amount
                    order_results.append(
                        OrderExecutionResult(
                            order_id=order.order_id,
                            status="failed",
                            message="win5_details missing",
                            amount=order.amount,
                            race_id=order.race_id,
                            ticket_type=order.ticket_type,
                        )
                    )
                    continue

                blocking_reason = self._cutoff_reason(order, request.timestamp)
                if blocking_reason:
                    skipped_amount += order.amount
                    order_results.append(
                        OrderExecutionResult(
                            order_id=order.order_id,
                            status="skipped",
                            message=blocking_reason,
                            amount=order.amount,
                            race_id=order.race_id,
                            ticket_type=order.ticket_type,
                        )
                    )
                    continue

                purchase_limit = driver.get_purchase_limit()
                if purchase_limit_before is None:
                    purchase_limit_before = purchase_limit
                purchase_limit_after = purchase_limit
                active_mode = bankroll_mode or "fixed"
                if resolved_amounts is None:
                    effective_amounts, active_mode = self._resolve_effective_amounts([order], purchase_limit)
                else:
                    effective_amounts = {order.order_id: resolved_amounts.get(order.order_id, 0)}
                effective_amount = effective_amounts.get(order.order_id, 0)
                if purchase_limit is not None and effective_amount <= 0:
                    skipped_amount += order.amount
                    order_results.append(
                        OrderExecutionResult(
                            order_id=order.order_id,
                            status="skipped",
                            message=f"purchase limit shortage: required={order.amount} available={purchase_limit}",
                            amount=order.amount,
                            race_id=order.race_id,
                            ticket_type=order.ticket_type,
                        )
                    )
                    continue
                if purchase_limit is not None and active_mode == "fixed" and purchase_limit < order.amount:
                    skipped_amount += order.amount
                    order_results.append(
                        OrderExecutionResult(
                            order_id=order.order_id,
                            status="skipped",
                            message=f"purchase limit shortage: required={order.amount} available={purchase_limit}",
                            amount=order.amount,
                            race_id=order.race_id,
                            ticket_type=order.ticket_type,
                        )
                    )
                    continue

                try:
                    if not driver.navigate_to_win5():
                        raise RuntimeError("WIN5 page navigation failed")

                    deadline = driver.get_win5_deadline()
                    deadline_reason = self._actual_deadline_reason(order, request.timestamp, deadline)
                    if deadline_reason:
                        skipped_amount += order.amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="skipped",
                                message=deadline_reason,
                                amount=order.amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                        continue

                    selections = [
                        order.win5_details.select_n1,
                        order.win5_details.select_n2,
                        order.win5_details.select_n3,
                        order.win5_details.select_n4,
                        order.win5_details.select_n5,
                    ]
                    success = driver.vote_win5(selections, effective_amount)
                    if success:
                        executed_amount += effective_amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="executed",
                                message=f"submitted win5 order mode={active_mode} total={effective_amount}",
                                amount=effective_amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    else:
                        failed_amount += effective_amount
                        order_results.append(
                            OrderExecutionResult(
                                order_id=order.order_id,
                                status="failed",
                                message="driver returned False",
                                amount=effective_amount,
                                race_id=order.race_id,
                                ticket_type=order.ticket_type,
                            )
                        )
                    refreshed_limit = driver.get_purchase_limit()
                    if refreshed_limit is not None:
                        purchase_limit_after = refreshed_limit
                except Exception as exc:
                    logger.exception("Failed WIN5 execution for order=%s", order.order_id)
                    failed_amount += effective_amount
                    order_results.append(
                        OrderExecutionResult(
                            order_id=order.order_id,
                            status="failed",
                            message=str(exc),
                            amount=effective_amount,
                            race_id=order.race_id,
                            ticket_type=order.ticket_type,
                        )
                    )
        except Exception as exc:
            logger.exception("Failed to initialize WIN5 driver")
            for order in orders:
                failed_amount += order.amount
                order_results.append(
                    OrderExecutionResult(
                        order_id=order.order_id,
                        status="failed",
                        message=str(exc),
                        amount=order.amount,
                        race_id=order.race_id,
                        ticket_type=order.ticket_type,
                    )
                )
        finally:
            if driver is not None:
                driver.close()

        return ExecutionSummary(
            status="done",
            message="win5 execution finished",
            executed_amount=executed_amount,
            skipped_amount=skipped_amount,
            failed_amount=failed_amount,
            purchase_limit_before=purchase_limit_before,
            purchase_limit_after=purchase_limit_after,
            order_results=order_results,
        )

    def _build_execution_batches(self, request: OrderRequest) -> list[ExecutionBatch]:
        grouped: dict[tuple[str, str], list[BetOrder]] = defaultdict(list)
        for order in sorted(request.orders, key=lambda item: (item.post_time, item.race_id, item.ticket_type, item.order_id)):
            grouped[(order.race_id, order.ticket_type)].append(order)

        return [
            ExecutionBatch(
                race_id=race_id,
                ticket_type=ticket_type,
                post_time=orders[0].post_time,
                orders=orders,
            )
            for (race_id, ticket_type), orders in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))
        ]

    def _group_batches_into_cohorts(self, batches: list[ExecutionBatch]) -> list[list[ExecutionBatch]]:
        grouped: dict[tuple[str, int], list[ExecutionBatch]] = defaultdict(list)
        for batch in batches:
            grouped[(batch.race_id[:8], batch.race_number)].append(batch)

        return sorted(
            grouped.values(),
            key=lambda cohort: min(batch.post_time for batch in cohort),
        )

    def _plan_cohort_amounts(
        self,
        cohort: list[ExecutionBatch],
        purchase_limit: int | None,
        use_json_amounts: bool,
    ) -> tuple[dict[str, dict[str, int]], str]:
        if use_json_amounts or purchase_limit is None:
            return (
                {
                    batch.batch_id: {order.order_id: order.amount for order in batch.orders}
                    for batch in cohort
                },
                "fixed-first" if use_json_amounts else "fixed",
            )

        bankroll_mode = self._get_bankroll_mode(purchase_limit)
        if bankroll_mode == "fixed":
            return (
                {
                    batch.batch_id: {order.order_id: order.amount for order in batch.orders}
                    for batch in cohort
                },
                bankroll_mode,
            )

        available_amount = max(0, (purchase_limit // 100) * 100)
        batch_allocations = self._allocate_batch_amounts(cohort, available_amount)
        return (
            {
                batch.batch_id: self._allocate_proportionally(batch.orders, batch_allocations.get(batch.batch_id, 0))
                for batch in cohort
            },
            bankroll_mode,
        )

    def _allocate_batch_amounts(self, batches: list[ExecutionBatch], total_amount: int) -> dict[str, int]:
        if total_amount <= 0 or not batches:
            return {batch.batch_id: 0 for batch in batches}

        unit_budget = total_amount // 100
        requested_units = sum(batch.requested_amount for batch in batches) / 100
        if unit_budget <= 0 or requested_units <= 0:
            return {batch.batch_id: 0 for batch in batches}

        allocation_units: dict[str, int] = {}
        remainders: list[tuple[float, str]] = []
        used_units = 0
        for batch in batches:
            exact_units = unit_budget * (batch.requested_amount / 100) / requested_units
            floor_units = math.floor(exact_units)
            allocation_units[batch.batch_id] = floor_units
            used_units += floor_units
            remainders.append((exact_units - floor_units, batch.batch_id))

        remaining_units = unit_budget - used_units
        for _, batch_id in sorted(remainders, reverse=True):
            if remaining_units <= 0:
                break
            allocation_units[batch_id] += 1
            remaining_units -= 1

        return {batch_id: units * 100 for batch_id, units in allocation_units.items()}

    def _wait_until_preparation_window(self, cohort: list[ExecutionBatch], request_timestamp: datetime) -> None:
        target_time = min(self._scheduled_datetime(batch.orders[0], request_timestamp) for batch in cohort)
        if target_time is None:
            return
        preparation_time = target_time - timedelta(minutes=self.config.cutoff_minutes)
        while True:
            now = datetime.now(request_timestamp.tzinfo)
            remaining = (preparation_time - now).total_seconds()
            if remaining <= 0:
                return
            sleep_seconds = min(30, max(1, int(remaining)))
            logger.info("Waiting for preparation window: %s seconds remaining", sleep_seconds)
            time_module.sleep(sleep_seconds)

    def _subset_request(self, request: OrderRequest, orders: list[BetOrder]) -> OrderRequest:
        return OrderRequest(
            version=request.version,
            sender=request.sender,
            request_id=request.request_id,
            timestamp=request.timestamp,
            orders=orders,
            raw=request.raw,
        )

    def _peek_purchase_limit(self) -> int | None:
        driver: IPATVoteDriver | None = None
        try:
            driver = IPATVoteDriver()
            driver.start()
            driver.login()
            driver.select_normal_bet()
            return driver.get_purchase_limit()
        except Exception:
            logger.exception("Failed to peek purchase limit")
            return None
        finally:
            if driver is not None:
                driver.close()

    def _parse_race_id(self, race_id: str) -> tuple[str, int]:
        jyo_code = race_id[8:10]
        race_num = int(race_id[-2:])
        jyo_name = JYO_CODE_MAP.get(jyo_code)
        if not jyo_name:
            raise ValueError(f"unsupported race_id venue code: {jyo_code}")
        return jyo_name, race_num

    def _unsupported_normal_bet_message(self, ticket_type: str) -> str | None:
        if ticket_type not in NORMAL_BET_TYPE_MAP:
            return f"ticket_type {ticket_type} is not supported by the current normal-bet driver"
        return None

    def _resolve_effective_amounts(
        self,
        orders: list[BetOrder],
        purchase_limit: int | None,
    ) -> tuple[dict[str, int], str]:
        requested_amounts = {order.order_id: order.amount for order in orders}
        if purchase_limit is None:
            return requested_amounts, "fixed"

        bankroll_mode = self._get_bankroll_mode(purchase_limit)
        if bankroll_mode == "fixed":
            return requested_amounts, bankroll_mode

        available_amount = max(0, (purchase_limit // 100) * 100)
        return self._allocate_proportionally(orders, available_amount), bankroll_mode

    def _get_bankroll_mode(self, purchase_limit: int) -> str:
        target_amount = int(self.config.target_balance_amount)
        if target_amount > 0 and purchase_limit < target_amount:
            return "rollover"
        return "fixed"

    def _allocate_proportionally(self, orders: list[BetOrder], total_amount: int) -> dict[str, int]:
        if total_amount <= 0 or not orders:
            return {order.order_id: 0 for order in orders}

        unit_budget = total_amount // 100
        if unit_budget <= 0:
            return {order.order_id: 0 for order in orders}

        total_requested_units = sum(order.amount for order in orders) / 100
        if total_requested_units <= 0:
            return {order.order_id: 0 for order in orders}

        allocation_units: dict[str, int] = {}
        remainders: list[tuple[float, str]] = []
        used_units = 0

        for order in orders:
            exact_units = unit_budget * (order.amount / 100) / total_requested_units
            floor_units = math.floor(exact_units)
            allocation_units[order.order_id] = floor_units
            used_units += floor_units
            remainders.append((exact_units - floor_units, order.order_id))

        remaining_units = unit_budget - used_units
        for _, order_id in sorted(remainders, reverse=True):
            if remaining_units <= 0:
                break
            allocation_units[order_id] += 1
            remaining_units -= 1

        return {order_id: units * 100 for order_id, units in allocation_units.items()}

    def _cutoff_reason(self, order: BetOrder, request_timestamp: datetime) -> str | None:
        if self.config.simulate_ipat and self.config.simulation_ignore_cutoff:
            return None
        scheduled_at = self._scheduled_datetime(order, request_timestamp)
        if scheduled_at is None:
            return None
        cutoff_at = scheduled_at - timedelta(minutes=self.config.cutoff_minutes)
        now = datetime.now(request_timestamp.tzinfo)
        if now >= cutoff_at:
            return f"cutoff reached: now={now.strftime('%H:%M')} cutoff={cutoff_at.strftime('%H:%M')}"
        return None

    def _actual_deadline_reason(
        self,
        order: BetOrder,
        request_timestamp: datetime,
        deadline_text: str,
    ) -> str | None:
        try:
            hour_str, minute_str = deadline_text.split(":", 1)
            deadline_at = self._scheduled_datetime(order, request_timestamp)
            if deadline_at is None:
                return None
            deadline_at = deadline_at.replace(hour=int(hour_str), minute=int(minute_str))
            cutoff_at = deadline_at - timedelta(minutes=self.config.cutoff_minutes)
            now = datetime.now(request_timestamp.tzinfo)
            if now >= cutoff_at:
                return f"actual WIN5 cutoff reached: now={now.strftime('%H:%M')} cutoff={cutoff_at.strftime('%H:%M')}"
        except Exception:
            logger.warning("Failed to parse actual WIN5 deadline: %s", deadline_text)
        return None

    def _scheduled_datetime(self, order: BetOrder, request_timestamp: datetime) -> datetime | None:
        try:
            race_date = datetime.strptime(order.race_id[:8], "%Y%m%d").date()
            return datetime.combine(race_date, order.post_time, tzinfo=request_timestamp.tzinfo)
        except ValueError:
            return None