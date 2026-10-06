import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.synthetic.validate import validate_dir  # noqa: E402

if __name__ == "__main__":
    report = validate_dir(Path(sys.argv[1] if len(sys.argv) > 1 else "data"))
    for c in report["checks"]:
        print(("PASS " if c["passed"] else "FAIL ") + c["check"] + (f"  [{c['detail']}]" if c["detail"] and not c["passed"] else ""))
    print(f"{report['checks_run'] - report['checks_failed']}/{report['checks_run']} checks passed")
    sys.exit(0 if report["passed"] else 1)
