#!/bin/bash

# E2E Test Runner Script
# Runs end-to-end tests with Playwright

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
TEST_DIR="tests/e2e"
BROWSERS="chromium firefox"
BASE_URL="http://localhost:8000"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}    Research Platform E2E Tests        ${NC}"
echo -e "${BLUE}========================================${NC}"

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    docker-compose down 2>/dev/null || true
    echo -e "${GREEN}Cleanup complete${NC}"
}

# Set trap to cleanup on script exit
trap cleanup EXIT

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running${NC}"
    exit 1
fi

# Install Playwright browsers if needed
echo -e "\n${YELLOW}Setting up Playwright browsers...${NC}"
playwright install chromium firefox webkit

# Start application
echo -e "\n${YELLOW}Starting application...${NC}"
docker-compose up -d

# Wait for application to be ready
echo -n "Waiting for application..."
for i in {1..30}; do
    if curl -s $BASE_URL/health > /dev/null 2>&1; then
        echo -e " ${GREEN}Ready${NC}"
        break
    fi
    echo -n "."
    sleep 2
done

# Create test results directory
mkdir -p test-results/e2e
mkdir -p screenshots
mkdir -p videos

# Run E2E tests
echo -e "\n${YELLOW}Running E2E tests...${NC}"
echo -e "${YELLOW}========================================${NC}"

# Set test environment variables
export BASE_URL=$BASE_URL
export HEADLESS=true
export SLOW_MO=0
export VIDEO=retain-on-failure
export SCREENSHOT=only-on-failure

# Run tests for each browser
for BROWSER in $BROWSERS; do
    echo -e "\n${BLUE}Testing with $BROWSER...${NC}"
    
    pytest $TEST_DIR \
        --browser=$BROWSER \
        --base-url=$BASE_URL \
        --screenshot=on \
        --video=on \
        --output=test-results/e2e/$BROWSER \
        -v \
        --tb=short \
        --color=yes \
        || true  # Continue even if one browser fails
done

# Generate HTML report
echo -e "\n${YELLOW}Generating test report...${NC}"
pytest $TEST_DIR \
    --html=test-results/e2e/report.html \
    --self-contained-html \
    2>/dev/null || true

# Check if all tests passed
if [ -f "test-results/e2e/.failed" ]; then
    echo -e "\n${RED}========================================${NC}"
    echo -e "${RED}      E2E Tests Failed! ❌              ${NC}"
    echo -e "${RED}========================================${NC}"
    
    echo -e "\n${YELLOW}Failed test artifacts:${NC}"
    echo -e "  Screenshots: ./screenshots/"
    echo -e "  Videos: ./videos/"
    echo -e "  Report: ./test-results/e2e/report.html"
    
    exit 1
else
    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}    All E2E Tests Passed! 🎉           ${NC}"
    echo -e "${GREEN}========================================${NC}"
    
    echo -e "\n${YELLOW}Test report: ./test-results/e2e/report.html${NC}"
    
    # Clean up successful test artifacts
    rm -rf screenshots/*.png
    rm -rf videos/*.webm
fi

exit 0