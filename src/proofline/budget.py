"""A hard, persistent cap on real model calls. The ledger is one JSON file shared by every process and every run that points at it
(MODEL_CALL_BUDGET_FILE): a call is counted BEFORE it is sent and is never refunded, so a failed request cannot be retried past the
cap. If the ledger cannot be read, validated or written, the call is refused: no ledger, no call.

    python -m proofline.budget init FILE LIMIT     create the ledger (refuses to overwrite: no accidental reset)
    python -m proofline.budget status FILE"""
import fcntl
import json
import os
import sys
from pathlib import Path

ENV = "MODEL_CALL_BUDGET_FILE"


class BudgetError(Exception):
    pass


def init(path: str | Path, limit: int) -> None:
    if type(limit) is not int or limit < 1:
        raise BudgetError("limit must be a positive integer")
    try:
        with open(path, "x") as f:  # "x": an existing ledger is never replaced, so the counter cannot be reset by accident
            json.dump({"limit": limit, "used": 0}, f)
    except FileExistsError:
        raise BudgetError("ledger already exists; refusing to reset it") from None
    except OSError:
        raise BudgetError("ledger cannot be created") from None


def reserve(path: str | Path) -> int:
    """Count one call before it is sent; returns the number of calls used so far, including this one."""
    try:
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)  # serialises threads and processes sharing the ledger
            d = json.load(f)
            limit, used = d["limit"], d["used"]
            if type(limit) is not int or type(used) is not int or limit < 1 or used < 0:
                raise BudgetError("ledger is corrupt")
            if used >= limit:
                raise BudgetError(f"budget of {limit} calls is spent")
            f.seek(0)
            f.truncate()
            json.dump({"limit": limit, "used": used + 1}, f)
            f.flush()
            os.fsync(f.fileno())
            return used + 1
    except BudgetError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise BudgetError("ledger is unusable") from None


def status(path: str | Path) -> dict:
    try:
        d = json.loads(Path(path).read_text())
        return {"limit": d["limit"], "used": d["used"], "remaining": d["limit"] - d["used"]}
    except (OSError, ValueError, KeyError, TypeError):
        raise BudgetError("ledger is unusable") from None


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["init"] and len(sys.argv) == 4:
            init(sys.argv[2], int(sys.argv[3]))
            print(status(sys.argv[2]))
        elif sys.argv[1:2] == ["status"] and len(sys.argv) == 3:
            print(status(sys.argv[2]))
        else:
            raise SystemExit(__doc__)
    except (BudgetError, ValueError) as e:
        raise SystemExit(f"error: {e}")
