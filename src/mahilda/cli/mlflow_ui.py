#!/usr/bin/env python3
"""
MLflow UI server launcher.
Provides a web interface for viewing experiments without gunicorn worker issues.
"""

import argparse
import importlib.util
import logging
import os
import subprocess
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Start MLflow UI server."""

    parser = argparse.ArgumentParser(description="Launch MLflow UI.")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind MLflow UI (default: 5000)")
    args = parser.parse_args(argv)

    # Get the mlruns directory
    mlruns_dir = Path("mlruns").absolute()
    mlruns_dir.mkdir(exist_ok=True)

    # Set up tracking URI
    tracking_uri = f"file://{mlruns_dir}"

    logger.info("=" * 60)
    logger.info("MLflow UI Server")
    logger.info("=" * 60)
    logger.info(f"Backend store URI: {tracking_uri}")
    logger.info("")
    logger.info("Launching MLflow UI...")
    logger.info("")

    try:
        if importlib.util.find_spec("mlflow") is None:
            raise ImportError

        # Configure environment
        os.environ["MLFLOW_BACKEND_STORE_URI"] = tracking_uri
        os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = str(mlruns_dir / "artifacts")

        # Create artifact directory if needed
        artifact_dir = mlruns_dir / "artifacts"
        artifact_dir.mkdir(exist_ok=True)

        # Launch MLflow UI
        host = "127.0.0.1"
        port = args.port

        logger.info(f"MLflow UI will be available at: http://{host}:{port}")
        logger.info("")
        logger.info("Press Ctrl+C to stop the server")
        logger.info("=" * 60)
        logger.info("")

        # Run the MLflow UI command
        cmd = [
            "mlflow",
            "ui",
            "--backend-store-uri",
            tracking_uri,
            "--host",
            host,
            "--port",
            str(port),
            "--app-name",
            "mlflow",
        ]

        subprocess.run(cmd, check=False)
        return 0

    except ImportError:
        logger.error("MLflow is not installed. Install it with: pip install mlflow")
        return 1
    except KeyboardInterrupt:
        logger.info("\nMLflow UI server stopped.")
        return 0
    except Exception as e:
        logger.error(f"Error starting MLflow UI: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
