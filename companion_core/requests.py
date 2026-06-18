"""Request persistence shared by lifecycle code and CLI wrappers."""

from __future__ import annotations

import fcntl
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RequestProposal:
    type: str
    title: str
    body: str
    priority: str = "normal"
    requested_time: str | None = None


VALID_REQUEST_TYPES = {
    "emergency_wakeup",
    "wakeup_request",
    "action",
    "fyi",
    "idea",
    "system_suggestion",
}
VALID_PRIORITIES = {"low", "normal", "high"}


def load_requests(requests_file: Path) -> list[dict]:
    try:
        return json.loads(requests_file.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def write_requests(requests_file: Path, requests: list[dict]) -> None:
    tmp_path = requests_file.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(requests, indent=2))
    tmp_path.replace(requests_file)


def save_requests(requests_file: Path, requests: list[dict]) -> None:
    requests_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file = requests_file.with_suffix(".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            write_requests(requests_file, requests)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def update_requests(requests_file: Path, mutator: Callable[[list[dict]], object]) -> object:
    requests_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file = requests_file.with_suffix(".lock")
    with open(lock_file, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            requests = load_requests(requests_file)
            result = mutator(requests)
            if result is not False:
                write_requests(requests_file, requests)
            return result
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def create_request(
    requests_file: Path,
    proposal: RequestProposal,
    *,
    waking_number: int | None = None,
) -> dict:
    if proposal.type not in VALID_REQUEST_TYPES:
        raise ValueError(f"invalid request type: {proposal.type}")
    if proposal.priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid request priority: {proposal.priority}")

    status = "self_approved" if proposal.type == "emergency_wakeup" else "pending"
    request = {
        "id": f"req_{time.time_ns()}_{uuid.uuid4().hex[:8]}",
        "created": datetime.now().isoformat(),
        "type": proposal.type,
        "title": proposal.title,
        "body": proposal.body,
        "requested_time": proposal.requested_time,
        "status": status,
        "priority": proposal.priority,
        "sophie_response": None,
        "scheduled_at": None,
        "resolved_at": None,
        "waking_number": waking_number,
    }
    if proposal.type == "system_suggestion":
        request["trial_period"] = None
        request["trial_review_date"] = None

    def append_request(requests: list[dict]) -> dict:
        requests.append(request)
        return request

    return update_requests(requests_file, append_request)
