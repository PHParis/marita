#!/usr/bin/env python3
"""MLflow tracking server launcher."""

import importlib.util
import logging
import os
import subprocess
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Start MLflow tracking server with file backend."""
    del argv

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
        if importlib.util.find_spec("mlflow") is None:
            raise ImportError

        os.environ["MLFLOW_BACKEND_STORE_URI"] = tracking_uri

        artifact_dir = mlruns_dir / "artifacts"
        artifact_dir.mkdir(exist_ok=True)
        artifact_root = str(artifact_dir.absolute())
        os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = artifact_root

        host = "127.0.0.1"
        port = 5000
        cmd = [
            "mlflow",
            "server",
            "--backend-store-uri",
            tracking_uri,
            "--default-artifact-root",
            artifact_root,
            "--host",
            host,
            "--port",
            str(port),
        ]

        logger.info("Launching MLflow tracking server...")
        logger.info(f"MLflow Tracking URI: {tracking_uri}")
        logger.info(f"MLflow UI URL: http://{host}:{port}")
        logger.info("Press Ctrl+C to stop the server")

        process = subprocess.Popen(cmd)
        try:
            return process.wait()
        except KeyboardInterrupt:
            logger.info("\nStopping MLflow server...")
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            logger.info("MLflow server stopped.")
            return 0

    except ImportError:
        logger.error("MLflow is not installed. Install it with: pip install mlflow")
        return 1
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
