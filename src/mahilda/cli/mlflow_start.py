#!/usr/bin/env python3
"""
Simple MLflow tracking server launcher.
Uses a built-in server without gunicorn workers to avoid SIGSEGV crashes.
"""

import logging
import os
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Start MLflow tracking server with file backend."""

    # Create mlruns directory if it doesn't exist
    mlruns_dir = Path("mlruns")
    mlruns_dir.mkdir(exist_ok=True)

    # Set up tracking URI to use file backend
    tracking_uri = f"file://{mlruns_dir.absolute()}"

    logger.info("=" * 60)
    logger.info("MLflow Tracking Server - Simple Mode")
    logger.info("=" * 60)
    logger.info(f"Using tracking URI: {tracking_uri}")
    logger.info(f"Backend directory: {mlruns_dir.absolute()}")
    logger.info("")

    try:
        # Import MLflow
        import mlflow

        logger.info("MLflow loaded successfully")

        # Configure MLflow to use file backend
        os.environ["MLFLOW_BACKEND_STORE_URI"] = tracking_uri
        os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = str(mlruns_dir.absolute() / "artifacts")

        # Create artifact directory
        artifact_dir = mlruns_dir / "artifacts"
        artifact_dir.mkdir(exist_ok=True)

        # Set MLflow tracking URI
        mlflow.set_tracking_uri(tracking_uri)

        # Create default experiment if it doesn't exist
        mlflow.set_experiment("Rule Discovery")

        logger.info("MLflow configured successfully")
        logger.info("")
        logger.info("You can now use MLflow tracking in your scripts:")
        logger.info("")
        logger.info("  import mlflow")
        logger.info("  mlflow.set_tracking_uri('file://$(pwd)/mlruns')")
        logger.info("  mlflow.set_experiment('Rule Discovery')")
        logger.info("  mlflow.start_run()")
        logger.info("  mlflow.log_param('param_name', param_value)")
        logger.info("  mlflow.log_metric('metric_name', metric_value)")
        logger.info("  mlflow.end_run()")
        logger.info("")
        logger.info("=" * 60)
        logger.info("MLflow server is ready!")
        logger.info("=" * 60)
        logger.info("")
        logger.info("To view your experiments, run:")
        logger.info(f"  mlflow ui --backend-store-uri {tracking_uri}")
        logger.info("")
        logger.info("Or use the simple MLflow UI server:")
        logger.info("  uv run mahilda mlflow ui")
        logger.info("")
        logger.info("Press Ctrl+C to exit")
        logger.info("=" * 60)

        # Keep the process running
        import time

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("\nMLflow server stopped.")
        return 0

    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.error("MLflow is not installed. Install it with: pip install mlflow")
        return 1
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
