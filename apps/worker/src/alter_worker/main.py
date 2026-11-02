import argparse

from alter_domain.runtime import run_mock_process


def main() -> None:
    parser = argparse.ArgumentParser(description="Alter worker scaffold")
    parser.add_argument("--once", action="store_true", help="Run one mock cycle and exit")
    args = parser.parse_args()
    run_mock_process("worker", once=args.once)
