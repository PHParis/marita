#!/bin/bash

# Simple MLflow server launcher script
# Avoids gunicorn worker crashes by using built-in Flask development server

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}MLflow Tracking Server - Simplified${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is not installed${NC}"
    exit 1
fi

# Check if MLflow is installed
if ! python3 -c "import mlflow" 2>/dev/null; then
    echo -e "${RED}Error: MLflow is not installed${NC}"
    echo -e "${YELLOW}Install with: pip install mlflow${NC}"
    exit 1
fi

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Create mlruns directory if needed
mkdir -p mlruns/artifacts

echo -e "${GREEN}Starting MLflow server...${NC}"
echo -e "${YELLOW}MLflow UI: http://localhost:5000${NC}"
echo ""
echo -e "${YELLOW}Backend: File-based storage${NC}"
echo -e "${YELLOW}Directory: $(pwd)/mlruns${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
echo ""

# Start MLflow server with file-based backend and single-threaded mode
# This avoids the gunicorn worker issues entirely
export MLFLOW_BACKEND_STORE_URI="file://$(pwd)/mlruns"
export MLFLOW_DEFAULT_ARTIFACT_ROOT="file://$(pwd)/mlruns/artifacts"

# Use the simple Python launcher
python3 start_mlflow.py
