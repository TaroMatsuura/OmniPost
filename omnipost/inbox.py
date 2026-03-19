from __future__ import annotations

import csv
import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import OmniPostConfig
from .executor import ExecutionSummary, IPATExecutionService
from .models import OrderRequest, OrderValidationError, parse_order_request

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessResult:
    file_path: Path
    status: str
    message: str
    request: OrderRequest | None = None
    archived_to: Path | None = None
    execution: ExecutionSummary | None = None


class InboxMonitor:
    def __init__(self, config: OmniPostConfig):
        self.config = config
        self.executor = IPATExecutionService(config)
        self.report_jsonl = self.config.logs_dir / "omnipost_inbox_report.jsonl"
        self.report_csv = self.config.logs_dir / "omnipost_inbox_report.csv"
        self.processed_request_ids = self._load_processed_request_ids()

    def process_pending_files(self) -> list[ProcessResult]:
        results: list[ProcessResult] = []
        for file_path in self._candidate_files():
            result = self._process_file(file_path)
            results.append(result)
        return results

    def watch_forever(self) -> None:
        logger.info("OmniPost inbox monitor started: %s", self.config.inbox_dir)
        while True:
            self.process_pending_files()
            time.sleep(self.config.poll_interval_sec)

    def _candidate_files(self) -> list[Path]:
        now = time.time()
        candidates: list[Path] = []
        for file_path in sorted(self.config.inbox_dir.glob("*.json")):
            if not file_path.is_file():
                continue
            age = now - file_path.stat().st_mtime
            if age < self.config.stable_file_age_sec:
                continue
            candidates.append(file_path)
        return candidates

    def _process_file(self, file_path: Path) -> ProcessResult:
        logger.info("Processing inbox file: %s", file_path.name)
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)

            request = parse_order_request(payload)
            if request.request_id in self.processed_request_ids:
                message = f"duplicate request_id skipped: {request.request_id}"
                result = ProcessResult(
                    file_path=file_path,
                    status="duplicate",
                    message=message,
                    request=request,
                )
                archived_to = self._archive_file(file_path, self.config.rejected_dir, payload, result)
                result.archived_to = archived_to
                logger.warning("Skipped duplicate %s: %s", file_path.name, message)
            else:
                execution = self.executor.execute_request(request)
                message = (
                    f"validated {len(request.orders)} order(s), total={request.total_amount}, "
                    f"{execution.message}"
                )
                result = ProcessResult(
                    file_path=file_path,
                    status="accepted",
                    message=message,
                    request=request,
                    execution=execution,
                )
                archived_to = self._archive_file(file_path, self.config.accepted_dir, payload, result)
                result.archived_to = archived_to
                self.processed_request_ids.add(request.request_id)
                logger.info("Accepted %s: %s", file_path.name, message)
        except (json.JSONDecodeError, OrderValidationError) as exc:
            result = ProcessResult(
                file_path=file_path,
                status="rejected",
                message=str(exc),
            )
            archived_to = self._archive_file(file_path, self.config.rejected_dir, None, result)
            result.archived_to = archived_to
            logger.warning("Rejected %s: %s", file_path.name, exc)

        self._write_report(result)
        return result

    def _archive_file(
        self,
        source_path: Path,
        destination_dir: Path,
        payload: dict | None,
        result: ProcessResult,
    ) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path = destination_dir / source_path.name
        if destination_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            destination_path = destination_dir / f"{source_path.stem}_{timestamp}{source_path.suffix}"

        if payload is not None:
            payload_to_archive = dict(payload)
            payload_to_archive["omnipost_result"] = self._build_archive_result(result)
            with destination_path.open("w", encoding="utf-8") as handle:
                json.dump(payload_to_archive, handle, ensure_ascii=False, indent=2)
            source_path.unlink(missing_ok=True)
        else:
            shutil.move(str(source_path), str(destination_path))
        return destination_path

    def _build_archive_result(self, result: ProcessResult) -> dict[str, object]:
        request = result.request
        return {
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "status": result.status,
            "message": result.message,
            "request_id": request.request_id if request else None,
            "sender": request.sender if request else None,
            "total_amount": request.total_amount if request else 0,
            "race_ids": request.race_ids if request else [],
            "order_ids": [order.order_id for order in request.orders] if request else [],
            "execution": self._serialize_execution(result.execution),
        }

    def _serialize_execution(self, execution: ExecutionSummary | None) -> dict[str, object] | None:
        if execution is None:
            return None
        return {
            "status": execution.status,
            "message": execution.message,
            "executed_amount": execution.executed_amount,
            "skipped_amount": execution.skipped_amount,
            "failed_amount": execution.failed_amount,
            "purchase_limit_before": execution.purchase_limit_before,
            "purchase_limit_after": execution.purchase_limit_after,
            "orders": [
                {
                    "order_id": order_result.order_id,
                    "status": order_result.status,
                    "message": order_result.message,
                    "amount": order_result.amount,
                    "race_id": order_result.race_id,
                    "ticket_type": order_result.ticket_type,
                }
                for order_result in execution.order_results
            ],
        }

    def _write_report(self, result: ProcessResult) -> None:
        record = {
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "source_file": result.file_path.name,
            "status": result.status,
            "message": result.message,
            "archived_to": str(result.archived_to) if result.archived_to else "",
            "sender": result.request.sender if result.request else "",
            "request_id": result.request.request_id if result.request else "",
            "version": result.request.version if result.request else "",
            "request_timestamp": result.request.timestamp.isoformat() if result.request else "",
            "race_ids": "|".join(result.request.race_ids) if result.request else "",
            "order_ids": "|".join(order.order_id for order in result.request.orders) if result.request else "",
            "ticket_types": "|".join(order.ticket_type for order in result.request.orders) if result.request else "",
            "order_count": len(result.request.orders) if result.request else 0,
            "total_amount": result.request.total_amount if result.request else 0,
            "execution_status": result.execution.status if result.execution else "",
            "execution_message": result.execution.message if result.execution else "",
            "executed_amount": result.execution.executed_amount if result.execution else 0,
            "skipped_amount": result.execution.skipped_amount if result.execution else 0,
            "failed_amount": result.execution.failed_amount if result.execution else 0,
        }

        with self.report_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        csv_exists = self.report_csv.exists()
        with self.report_csv.open("a", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(record.keys()))
            if not csv_exists:
                writer.writeheader()
            writer.writerow(record)

    def _load_processed_request_ids(self) -> set[str]:
        if not self.report_jsonl.exists():
            return set()

        request_ids: set[str] = set()
        with self.report_jsonl.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                request_id = record.get("request_id")
                if isinstance(request_id, str) and request_id:
                    request_ids.add(request_id)
        return request_ids