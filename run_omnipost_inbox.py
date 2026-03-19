#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from omnipost import InboxMonitor, OmniPostConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OmniPost inbox monitor: JSON を監視して検証・アーカイブします"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="1 回だけ inbox をスキャンして終了します",
    )
    return parser.parse_args()


def setup_logging(log_path: Path) -> None:
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers = [file_handler, stream_handler]


def main() -> int:
    load_dotenv()
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    config = OmniPostConfig.from_env(base_dir)
    setup_logging(config.logs_dir / "omnipost_inbox.log")

    monitor = InboxMonitor(config)
    if args.once:
        results = monitor.process_pending_files()
        accepted_count = sum(1 for result in results if result.status == "accepted")
        rejected_count = sum(1 for result in results if result.status == "rejected")
        executed_count = sum(1 for result in results if result.execution and result.execution.status == "executed")
        partial_count = sum(1 for result in results if result.execution and result.execution.status == "partial")
        failed_count = sum(1 for result in results if result.execution and result.execution.status == "failed")
        logging.info(
            "Scan complete: files=%s accepted=%s rejected=%s executed=%s partial=%s failed=%s",
            len(results),
            accepted_count,
            rejected_count,
            executed_count,
            partial_count,
            failed_count,
        )
        return 0

    try:
        monitor.watch_forever()
    except KeyboardInterrupt:
        logging.info("OmniPost inbox monitor stopped by user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())