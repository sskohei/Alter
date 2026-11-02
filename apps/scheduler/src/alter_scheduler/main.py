from alter_domain.runtime import run_mock_process


def main() -> None:
    # Cron is a short-lived process, separate from the API and worker.
    run_mock_process("scheduler", once=True)
