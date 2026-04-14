#!/bin/bash

# Cerebro Research Platform - Startup Script
# This script starts both the FastAPI backend and React frontend

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${BLUE}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                                                    ║${NC}"
echo -e "${BLUE}║     🧠 Cerebro Research Platform                   ║${NC}"
echo -e "${BLUE}║        Multi-Agent AI Research System              ║${NC}"
echo -e "${BLUE}║                                                    ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════╝${NC}"
echo ""

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}⚠️  Python virtual environment not found. Creating...${NC}"
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to create virtual environment${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Virtual environment created${NC}"
fi

# Activate virtual environment
echo -e "${CYAN}🐍 Activating Python virtual environment...${NC}"
source .venv/bin/activate

# Install/update backend dependencies
echo -e "${CYAN}📦 Checking backend dependencies...${NC}"
pip install -q -e ".[dev]" 2>&1 | grep -v "already satisfied" || true

# Check if web directory exists
if [ ! -d "web" ]; then
    echo -e "${YELLOW}⚠️  Frontend directory not found at ./web${NC}"
    echo -e "${YELLOW}   Frontend will not be started${NC}"
    START_FRONTEND=false
else
    START_FRONTEND=true
    
    # Check if node_modules exists
    if [ ! -d "web/node_modules" ]; then
        echo -e "${YELLOW}⚠️  Frontend dependencies not installed${NC}"
        echo -e "${CYAN}📦 Installing frontend dependencies...${NC}"
        cd web
        npm install
        if [ $? -ne 0 ]; then
            echo -e "${RED}❌ Failed to install frontend dependencies${NC}"
            START_FRONTEND=false
        fi
        cd ..
    fi
fi

# Function to cleanup processes on exit
cleanup() {
    echo -e "\n${YELLOW}🛑 Shutting down Cerebro...${NC}"
    
    if [ -n "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null
        wait $BACKEND_PID 2>/dev/null
        echo -e "${GREEN}  ✓ Backend stopped${NC}"
    fi
    
    if [ -n "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null
        wait $FRONTEND_PID 2>/dev/null
        echo -e "${GREEN}  ✓ Frontend stopped${NC}"
    fi
    
    echo -e "${GREEN}👋 Cerebro stopped. See you next time!${NC}"
    exit 0
}

# Set trap for cleanup on Ctrl+C
trap cleanup SIGINT SIGTERM

echo ""
echo -e "${CYAN}🚀 Starting services...${NC}"
echo ""

# Start backend
echo -e "${GREEN}▶ Starting FastAPI backend${NC}"
echo -e "   ${CYAN}URL: http://localhost:8000${NC}"
echo -e "   ${CYAN}Docs: http://localhost:8000/docs${NC}"
echo ""

uvicorn src.api.main:app --reload --port 8000 --log-level info > /tmp/cerebro-backend.log 2>&1 &
BACKEND_PID=$!

# Wait for backend to start
echo -e "${CYAN}⏳ Waiting for backend to initialize...${NC}"
for i in {1..30}; do
    if curl -s http://localhost:8000/api/v1/health > /dev/null 2>&1; then
        echo -e "${GREEN}✅ Backend is ready${NC}"
        break
    fi
    sleep 1
    echo -n "."
done

echo ""

# Check if backend started successfully
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "${RED}❌ Backend failed to start${NC}"
    echo -e "${YELLOW}   Check logs: /tmp/cerebro-backend.log${NC}"
    exit 1
fi

# Start frontend if available
if [ "$START_FRONTEND" = true ]; then
    echo -e "${GREEN}▶ Starting React frontend${NC}"
    echo -e "   ${CYAN}URL: http://localhost:5173${NC}"
    echo ""
    
    cd web
    npm run dev > /tmp/cerebro-frontend.log 2>&1 &
    FRONTEND_PID=$!
    cd ..
    
    # Wait for frontend to start
    echo -e "${CYAN}⏳ Waiting for frontend to initialize...${NC}"
    for i in {1..30}; do
        if curl -s http://localhost:5173 > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Frontend is ready${NC}"
            break
        fi
        sleep 1
        echo -n "."
    done
    echo ""
    
    # Check if frontend started successfully
    if ! kill -0 $FRONTEND_PID 2>/dev/null; then
        echo -e "${RED}❌ Frontend failed to start${NC}"
        echo -e "${YELLOW}   Check logs: /tmp/cerebro-frontend.log${NC}"
    fi
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                                                    ║${NC}"
echo -e "${GREEN}║     ✅ Cerebro is running!                         ║${NC}"
echo -e "${GREEN}║                                                    ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  🌐 Frontend: http://localhost:5173                ║${NC}"
echo -e "${GREEN}║  ⚡ Backend:  http://localhost:8000                ║${NC}"
echo -e "${GREEN}║  📚 API Docs: http://localhost:8000/docs           ║${NC}"
echo -e "${GREEN}║                                                    ║${NC}"
echo -e "${GREEN}╠════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  Press Ctrl+C to stop all services                 ║${NC}"
echo -e "${GREEN}║                                                    ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════╝${NC}"
echo ""

# Show logs from both processes
if [ "$START_FRONTEND" = true ]; then
    echo -e "${CYAN}📋 Showing logs (Backend + Frontend):${NC}"
    echo -e "${YELLOW}   Backend log:  /tmp/cerebro-backend.log${NC}"
    echo -e "${YELLOW}   Frontend log: /tmp/cerebro-frontend.log${NC}"
else
    echo -e "${CYAN}📋 Showing backend logs:${NC}"
    echo -e "${YELLOW}   Backend log: /tmp/cerebro-backend.log${NC}"
fi
echo ""

# Tail logs
if [ "$START_FRONTEND" = true ]; then
    tail -f /tmp/cerebro-backend.log /tmp/cerebro-frontend.log 2>/dev/null &
    TAIL_PID=$!
else
    tail -f /tmp/cerebro-backend.log 2>/dev/null &
    TAIL_PID=$!
fi

# Wait for processes
wait $BACKEND_PID $FRONTEND_PID 2>/dev/null

# Kill tail process
kill $TAIL_PID 2>/dev/null
