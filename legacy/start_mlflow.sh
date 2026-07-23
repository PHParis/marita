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

# Get the repo root directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "$REPO_ROOT"

# Create mlruns directory if needed
mkdir -p mlruns/artifacts

echo -e "${GREEN}Starting MLflow server...${NC}"
echo -e "${YELLOW}MLflow UI: http://localhost:5000${NC}"
echo ""
echo -e "${YELLOW}Backend: File-based storage${NC}"
echo -e "${YELLOW}Directory: ${REPO_ROOT}/mlruns${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
echo ""

# Use the package CLI launcher
uv run marita mlflow start
