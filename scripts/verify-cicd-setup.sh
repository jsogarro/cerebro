#!/bin/bash

# CI/CD Configuration Verification Script
# This script verifies that all CI/CD security configurations are properly set up

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters for summary
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0
WARNING_CHECKS=0

# Function to print colored output
print_header() {
    echo
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════${NC}"
}

check_pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASSED_CHECKS++))
    ((TOTAL_CHECKS++))
}

check_fail() {
    echo -e "${RED}✗${NC} $1"
    ((FAILED_CHECKS++))
    ((TOTAL_CHECKS++))
}

check_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    ((WARNING_CHECKS++))
    ((TOTAL_CHECKS++))
}

# Check if GitHub CLI is installed
if ! command -v gh &> /dev/null; then
    echo -e "${RED}[ERROR]${NC} GitHub CLI (gh) is not installed."
    echo "Please install it from: https://cli.github.com/"
    exit 1
fi

# Get repository information
if git remote get-url origin &> /dev/null; then
    REPO_URL=$(git remote get-url origin)
    if [[ $REPO_URL =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
        REPO_OWNER="${BASH_REMATCH[1]}"
        REPO_NAME="${BASH_REMATCH[2]}"
        REPO="$REPO_OWNER/$REPO_NAME"
    fi
else
    echo -e "${RED}[ERROR]${NC} Not in a git repository or no origin remote found."
    exit 1
fi

echo "================================================"
echo " CI/CD Configuration Verification"
echo " Repository: $REPO"
echo " Date: $(date '+%Y-%m-%d %H:%M:%S')"
echo "================================================"

# Authenticate with GitHub
gh auth status &> /dev/null || (echo -e "${RED}[ERROR]${NC} Not authenticated with GitHub. Please run 'gh auth login' first." && exit 1)

# 1. Check Repository Secrets
print_header "1. Repository Secrets"

REQUIRED_SECRETS=(
    "GCP_PROJECT_ID"
    "GCP_SA_KEY"
    "GKE_CLUSTER_NAME"
    "GKE_ZONE"
    "CODECOV_TOKEN"
    "PYPI_API_TOKEN"
    "SLACK_WEBHOOK_URL"
)

echo "Checking required secrets..."
for SECRET in "${REQUIRED_SECRETS[@]}"; do
    if gh secret list 2>/dev/null | grep -q "^$SECRET"; then
        check_pass "$SECRET is configured"
    else
        check_fail "$SECRET is NOT configured"
    fi
done

# 2. Check Branch Protection Rules
print_header "2. Branch Protection Rules"

echo "Checking main branch protection..."
if gh api "/repos/$REPO/branches/main/protection" 2>/dev/null > /tmp/main_protection.json; then
    check_pass "Main branch protection is enabled"
    
    # Check specific settings
    if jq -e '.required_status_checks.strict == true' /tmp/main_protection.json > /dev/null 2>&1; then
        check_pass "Strict status checks enabled for main"
    else
        check_warn "Strict status checks not enabled for main"
    fi
    
    if jq -e '.required_pull_request_reviews.required_approving_review_count >= 2' /tmp/main_protection.json > /dev/null 2>&1; then
        check_pass "Main requires 2+ approvals"
    else
        check_warn "Main requires less than 2 approvals"
    fi
    
    if jq -e '.enforce_admins.enabled == true' /tmp/main_protection.json > /dev/null 2>&1; then
        check_pass "Admin enforcement enabled for main"
    else
        check_warn "Admin enforcement not enabled for main"
    fi
else
    check_fail "Main branch protection is NOT enabled"
fi

echo
echo "Checking develop branch protection..."
if gh api "/repos/$REPO/branches/develop/protection" 2>/dev/null > /tmp/develop_protection.json; then
    check_pass "Develop branch protection is enabled"
    
    if jq -e '.required_pull_request_reviews.required_approving_review_count >= 1' /tmp/develop_protection.json > /dev/null 2>&1; then
        check_pass "Develop requires 1+ approval"
    else
        check_warn "Develop has no approval requirement"
    fi
else
    check_warn "Develop branch protection is NOT enabled (branch might not exist)"
fi

# 3. Check Environments
print_header "3. Environment Configuration"

echo "Checking environments..."
if gh api "/repos/$REPO/environments" 2>/dev/null > /tmp/environments.json; then
    if jq -e '.environments[] | select(.name == "staging")' /tmp/environments.json > /dev/null 2>&1; then
        check_pass "Staging environment exists"
    else
        check_fail "Staging environment NOT configured"
    fi
    
    if jq -e '.environments[] | select(.name == "production")' /tmp/environments.json > /dev/null 2>&1; then
        check_pass "Production environment exists"
        
        # Check production protection
        if gh api "/repos/$REPO/environments/production" 2>/dev/null | jq -e '.protection_rules | length > 0' > /dev/null 2>&1; then
            check_pass "Production environment has protection rules"
        else
            check_warn "Production environment lacks protection rules"
        fi
    else
        check_fail "Production environment NOT configured"
    fi
else
    check_fail "Could not retrieve environment information"
fi

# 4. Check Workflow Files
print_header "4. GitHub Actions Workflows"

REQUIRED_WORKFLOWS=(
    ".github/workflows/ci.yml"
    ".github/workflows/cd.yml"
    ".github/workflows/release.yml"
    ".github/workflows/docker-build.yml"
    ".github/workflows/temporal-test.yml"
    ".github/workflows/codeql.yml"
)

echo "Checking workflow files..."
for WORKFLOW in "${REQUIRED_WORKFLOWS[@]}"; do
    if [ -f "$WORKFLOW" ]; then
        check_pass "$(basename $WORKFLOW) exists"
        
        # Check if workflow is valid
        if gh workflow view "$(basename $WORKFLOW .yml)" &> /dev/null; then
            check_pass "$(basename $WORKFLOW) is valid"
        else
            check_warn "$(basename $WORKFLOW) might have syntax issues"
        fi
    else
        check_fail "$(basename $WORKFLOW) NOT found"
    fi
done

# 5. Check Security Features
print_header "5. Security Features"

echo "Checking repository security settings..."

# Check vulnerability alerts
if gh api "/repos/$REPO" 2>/dev/null | jq -e '.security_and_analysis.vulnerability_alerts.status == "enabled"' > /dev/null 2>&1; then
    check_pass "Vulnerability alerts enabled"
else
    check_warn "Vulnerability alerts not enabled"
fi

# Check secret scanning
if gh api "/repos/$REPO" 2>/dev/null | jq -e '.security_and_analysis.secret_scanning.status == "enabled"' > /dev/null 2>&1; then
    check_pass "Secret scanning enabled"
else
    check_warn "Secret scanning not enabled"
fi

# Check Dependabot
if [ -f ".github/dependabot.yml" ] || [ -f ".github/dependabot.yaml" ]; then
    check_pass "Dependabot configuration exists"
else
    check_warn "Dependabot configuration not found"
fi

# 6. Check Recent Workflow Runs
print_header "6. Recent Workflow Status"

echo "Checking recent workflow runs..."
if gh run list --limit 5 --json conclusion,name,status 2>/dev/null > /tmp/runs.json; then
    FAILED_RUNS=$(jq '[.[] | select(.conclusion == "failure")] | length' /tmp/runs.json)
    SUCCESS_RUNS=$(jq '[.[] | select(.conclusion == "success")] | length' /tmp/runs.json)
    
    if [ "$FAILED_RUNS" -eq 0 ]; then
        check_pass "No failed runs in recent history"
    else
        check_warn "$FAILED_RUNS failed run(s) in recent history"
    fi
    
    if [ "$SUCCESS_RUNS" -gt 0 ]; then
        check_pass "$SUCCESS_RUNS successful run(s) in recent history"
    else
        check_warn "No successful runs in recent history"
    fi
else
    check_warn "Could not retrieve workflow run history"
fi

# 7. Check Docker and Kubernetes Configuration
print_header "7. Container & Orchestration"

echo "Checking Docker configuration..."
if [ -f "Dockerfile" ]; then
    check_pass "Dockerfile exists"
else
    check_fail "Dockerfile NOT found"
fi

if [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ]; then
    check_pass "Docker Compose configuration exists"
else
    check_warn "Docker Compose configuration not found"
fi

echo
echo "Checking Kubernetes configuration..."
if [ -d "k8s" ]; then
    check_pass "Kubernetes manifests directory exists"
    
    K8S_FILES=$(find k8s -name "*.yaml" -o -name "*.yml" 2>/dev/null | wc -l)
    if [ "$K8S_FILES" -gt 0 ]; then
        check_pass "Found $K8S_FILES Kubernetes manifest file(s)"
    else
        check_warn "No Kubernetes manifest files found"
    fi
else
    check_fail "Kubernetes manifests directory NOT found"
fi

# Summary
print_header "Verification Summary"

COMPLETION_PERCENTAGE=$((PASSED_CHECKS * 100 / TOTAL_CHECKS))

echo "Total Checks: $TOTAL_CHECKS"
echo -e "${GREEN}Passed: $PASSED_CHECKS${NC}"
echo -e "${YELLOW}Warnings: $WARNING_CHECKS${NC}"
echo -e "${RED}Failed: $FAILED_CHECKS${NC}"
echo
echo "Completion: $COMPLETION_PERCENTAGE%"

# Overall status
echo
if [ "$FAILED_CHECKS" -eq 0 ]; then
    if [ "$WARNING_CHECKS" -eq 0 ]; then
        echo -e "${GREEN}════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  ✓ CI/CD CONFIGURATION IS FULLY COMPLETE!${NC}"
        echo -e "${GREEN}════════════════════════════════════════════════${NC}"
    else
        echo -e "${YELLOW}════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  ⚠ CI/CD configuration is functional with warnings${NC}"
        echo -e "${YELLOW}════════════════════════════════════════════════${NC}"
        echo
        echo "Review warnings above and consider addressing them."
    fi
else
    echo -e "${RED}════════════════════════════════════════════════${NC}"
    echo -e "${RED}  ✗ CI/CD configuration is INCOMPLETE${NC}"
    echo -e "${RED}════════════════════════════════════════════════${NC}"
    echo
    echo "Critical issues found. Please address failed checks above."
    echo "Refer to docs/ci-cd-security-configuration.md for setup instructions."
fi

# Cleanup
rm -f /tmp/main_protection.json /tmp/develop_protection.json /tmp/environments.json /tmp/runs.json

echo
echo "For detailed configuration instructions, see:"
echo "  docs/ci-cd-security-configuration.md"
echo
echo "To automatically configure protection rules, run:"
echo "  ./scripts/setup-github-protection.sh"