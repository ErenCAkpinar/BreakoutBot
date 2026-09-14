"""Dispatch a standard backtest or an explicitly selected research variant."""
from pathlib import Path
import os
import runpy
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    fraction = os.environ.get("BT_TP2_CLOSE_FRAC")
    if fraction is not None:
        from experiments.tp2_runner import install_runner

        install_runner(float(fraction))
        print(f"RESEARCH ONLY: TP2 close fraction={float(fraction):g}; "
              "residual keeps existing trail/deadline/portfolio slot.", flush=True)
        print("The config check below covers base geometry only; "
              "this exit variant is NOT deployed.", flush=True)
    sys.argv[0] = str(root / "backtest.py")
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
