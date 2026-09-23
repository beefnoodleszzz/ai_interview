"""Command-line entrypoint for the deployed AutoDL H3 worker."""

from ai_interview_h3_worker.worker import main

if __name__ == "__main__":
    raise SystemExit(main())
