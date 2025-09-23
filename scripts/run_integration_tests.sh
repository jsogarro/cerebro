#!/bin/bash

# Integration Test Runner Script
# Runs comprehensive integration tests with Docker services

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
COMPOSE_FILE="tests/integration/docker-compose.test.yml"
TEST_DIR="tests/integration"
COVERAGE_THRESHOLD=80

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   Research Platform Integration Tests  ${NC}"
echo -e "${GREEN}========================================${NC}"

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Cleaning up test environment...${NC}"
    docker-compose -f $COMPOSE_FILE down -v 2>/dev/null || true
    echo -e "${GREEN}Cleanup complete${NC}"
}

# Set trap to cleanup on script exit
trap cleanup EXIT

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}Error: Docker is not running${NC}"
    exit 1
fi

# Start test environment
echo -e "\n${YELLOW}Starting test environment...${NC}"
docker-compose -f $COMPOSE_FILE up -d

# Wait for services to be ready
echo -e "${YELLOW}Waiting for services to be ready...${NC}"

# Wait for PostgreSQL
echo -n "Waiting for PostgreSQL..."
for i in {1..30}; do
    if docker-compose -f $COMPOSE_FILE exec -T postgres pg_isready -U test_user -d test_research_db > /dev/null 2>&1; then
        echo -e " ${GREEN}Ready${NC}"
        break
    fi
    echo -n "."
    sleep 1
done

# Wait for Redis
echo -n "Waiting for Redis..."
for i in {1..30}; do
    if docker-compose -f $COMPOSE_FILE exec -T redis redis-cli ping > /dev/null 2>&1; then
        echo -e " ${GREEN}Ready${NC}"
        break
    fi
    echo -n "."
    sleep 1
done

# Wait for Temporal
echo -n "Waiting for Temporal..."
for i in {1..60}; do
    if docker-compose -f $COMPOSE_FILE exec -T temporal tctl --address temporal:7233 namespace list > /dev/null 2>&1; then
        echo -e " ${GREEN}Ready${NC}"
        break
    fi
    echo -n "."
    sleep 2
done

# Run database migrations
echo -e "\n${YELLOW}Running database migrations...${NC}"
export DATABASE_URL="postgresql://test_user:test_password@localhost:5433/test_research_db"
alembic upgrade head || echo "Migrations skipped (may not exist)"

# Run integration tests
echo -e "\n${YELLOW}Running integration tests...${NC}"
echo -e "${YELLOW}========================================${NC}"

# Set test environment variables
export ENVIRONMENT=test
export GEMINI_API_KEY=test-key
export TEMPORAL_HOST=localhost:7234
export REDIS_URL=redis://localhost:6380/15

# Run tests with coverage
pytest $TEST_DIR \
    --cov=src \
    --cov-report=html:htmlcov/integration \
    --cov-report=term-missing \
    --cov-fail-under=$COVERAGE_THRESHOLD \
    -v \
    --tb=short \
    --color=yes \
    --durations=10

TEST_EXIT_CODE=$?

# Show test results
if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo -e "\n${GREEN}========================================${NC}"
    echo -e "${GREEN}   All Integration Tests Passed! 🎉     ${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e "\n${YELLOW}Coverage report available at: htmlcov/integration/index.html${NC}"
else
    echo -e "\n${RED}========================================${NC}"
    echo -e "${RED}   Integration Tests Failed! ❌         ${NC}"
    echo -e "${RED}========================================${NC}"
    
    # Show container logs on failure
    echo -e "\n${YELLOW}Container logs:${NC}"
    docker-compose -f $COMPOSE_FILE logs --tail=50
fi

exit $TEST_EXIT_CODE