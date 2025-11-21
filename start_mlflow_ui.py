#!/usr/bin/env python3
"""
MLflow UI server launcher.
Provides a web interface for viewing experiments without gunicorn worker issues.
"""

import os
import sys
import logging
import subprocess
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Start MLflow UI server."""
    
    # Get the mlruns directory
    mlruns_dir = Path("mlruns").absolute()
    mlruns_dir.mkdir(exist_ok=True)
    
    # Set up tracking URI
    tracking_uri = f"file://{mlruns_dir}"
    
    logger.info("="*60)
    logger.info("MLflow UI Server")
    logger.info("="*60)
    logger.info(f"Backend store URI: {tracking_uri}")
    logger.info("")
    logger.info("Launching MLflow UI...")
    logger.info("")
    
    try:
        import mlflow
        
        # Configure environment
        os.environ['MLFLOW_BACKEND_STORE_URI'] = tracking_uri
        os.environ['MLFLOW_DEFAULT_ARTIFACT_ROOT'] = str(mlruns_dir / "artifacts")
        
        # Create artifact directory if needed
        artifact_dir = mlruns_dir / "artifacts"
        artifact_dir.mkdir(exist_ok=True)
        
        # Launch MLflow UI
        host = "127.0.0.1"
        port = 5000
        
        logger.info(f"MLflow UI will be available at: http://{host}:{port}")
        logger.info("")
        logger.info("Press Ctrl+C to stop the server")
        logger.info("="*60)
        logger.info("")
        
        # Run the MLflow UI command
        cmd = [
            "mlflow", "ui",
            "--backend-store-uri", tracking_uri,
            "--host", host,
            "--port", str(port),
            "--app-name", "mlflow"
        ]
        
        subprocess.run(cmd, check=False)
        
    except ImportError:
        logger.error("MLflow is not installed. Install it with: pip install mlflow")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("\nMLflow UI server stopped.")
    except Exception as e:
        logger.error(f"Error starting MLflow UI: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
