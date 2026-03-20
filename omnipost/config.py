from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(slots=True)
class OmniPostConfig:
    base_dir: Path
    inbox_dir: Path
    archive_dir: Path
    accepted_dir: Path
    rejected_dir: Path
    logs_dir: Path
    target_balance_amount: Decimal
    stop_target_balance_amount: Decimal
    execute_votes: bool
    simulate_ipat: bool
    force_json_amounts: bool
    skip_preparation_wait: bool
    simulation_purchase_limit: int
    simulation_ignore_cutoff: bool
    cutoff_minutes: int
    poll_interval_sec: float = 3.0
    stable_file_age_sec: float = 1.0

    @classmethod
    def from_env(cls, base_dir: Path) -> "OmniPostConfig":
        inbox_dir = Path(os.getenv("OMNIPOST_INBOX_DIR", base_dir / "inbox"))
        archive_dir = Path(os.getenv("OMNIPOST_ARCHIVE_DIR", base_dir / "archive"))
        accepted_dir = Path(os.getenv("OMNIPOST_ACCEPTED_DIR", archive_dir / "accepted"))
        rejected_dir = Path(os.getenv("OMNIPOST_REJECTED_DIR", archive_dir / "rejected"))
        logs_dir = Path(os.getenv("OMNIPOST_LOG_DIR", base_dir / "logs"))
        target_balance_amount = Decimal(os.getenv("TARGET_BALANCE_AMOUNT", "0"))
        stop_target_balance_amount = Decimal(os.getenv("OMNIPOST_STOP_TARGET_BALANCE_AMOUNT", "0"))
        execute_votes = os.getenv("OMNIPOST_EXECUTE_VOTES", "True") == "True"
        simulate_ipat = os.getenv("OMNIPOST_SIMULATE_IPAT", "False") == "True"
        force_json_amounts = os.getenv("OMNIPOST_FORCE_JSON_AMOUNTS", "False") == "True"
        skip_preparation_wait = os.getenv("OMNIPOST_SKIP_PREPARATION_WAIT", "False") == "True"
        simulation_purchase_limit = int(os.getenv("OMNIPOST_SIMULATED_PURCHASE_LIMIT", "9999999"))
        simulation_ignore_cutoff = os.getenv("OMNIPOST_SIMULATION_IGNORE_CUTOFF", "True") == "True"
        cutoff_minutes = int(os.getenv("OMNIPOST_CUTOFF_MINUTES", "5"))
        poll_interval_sec = float(os.getenv("OMNIPOST_POLL_INTERVAL_SEC", "3"))
        stable_file_age_sec = float(os.getenv("OMNIPOST_STABLE_FILE_AGE_SEC", "1"))

        config = cls(
            base_dir=base_dir,
            inbox_dir=inbox_dir,
            archive_dir=archive_dir,
            accepted_dir=accepted_dir,
            rejected_dir=rejected_dir,
            logs_dir=logs_dir,
            target_balance_amount=target_balance_amount,
            stop_target_balance_amount=stop_target_balance_amount,
            execute_votes=execute_votes,
            simulate_ipat=simulate_ipat,
            force_json_amounts=force_json_amounts,
            skip_preparation_wait=skip_preparation_wait,
            simulation_purchase_limit=simulation_purchase_limit,
            simulation_ignore_cutoff=simulation_ignore_cutoff,
            cutoff_minutes=cutoff_minutes,
            poll_interval_sec=poll_interval_sec,
            stable_file_age_sec=stable_file_age_sec,
        )
        config.ensure_directories()
        return config

    def ensure_directories(self) -> None:
        for directory in (
            self.inbox_dir,
            self.archive_dir,
            self.accepted_dir,
            self.rejected_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)