#!/bin/bash

# GitHub Branch Protection and Security Setup Script
# This script configures branch protection rules and repository settings for the Cerebro AI Brain platform
# Usage: ./setup-github-protection.sh [GITHUB_TOKEN] [REPO_OWNER] [REPO_NAME]

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if GitHub CLI is installed
if ! command -v gh &> /dev/null; then
    print_error "GitHub CLI (gh) is not installed. Please install it first."
    echo "Visit: https://cli.github.com/"
    exit 1
fi

# Get repository information
if [ -n "$2" ] && [ -n "$3" ]; then
    REPO_OWNER="$2"
    REPO_NAME="$3"
else
    # Try to get from current git repository
    if git remote get-url origin &> /dev/null; then
        REPO_URL=$(git remote get-url origin)
        if [[ $REPO_URL =~ github.com[:/]([^/]+)/([^/.]+) ]]; then
            REPO_OWNER="${BASH_REMATCH[1]}"
            REPO_NAME="${BASH_REMATCH[2]}"
        fi
    fi
fi

if [ -z "$REPO_OWNER" ] || [ -z "$REPO_NAME" ]; then
    print_error "Could not determine repository owner and name."
    echo "Usage: $0 [GITHUB_TOKEN] [REPO_OWNER] [REPO_NAME]"
    exit 1
fi

REPO="$REPO_OWNER/$REPO_NAME"
print_status "Configuring protection for repository: $REPO"

# Authenticate with GitHub
if [ -n "$1" ]; then
    export GITHUB_TOKEN="$1"
    print_status "Using provided GitHub token for authentication"
else
    print_status "Using existing GitHub CLI authentication"
    gh auth status || (print_error "Not authenticated. Please run 'gh auth login' first." && exit 1)
fi

# Function to configure branch protection
configure_branch_protection() {
    local BRANCH=$1
    local REQUIRE_REVIEWS=$2
    local REQUIRED_APPROVALS=$3
    
    print_status "Configuring protection for branch: $BRANCH"
    
    # Prepare the protection rules JSON
    if [ "$BRANCH" == "main" ]; then
        cat > protection_rules.json << EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "CI Pipeline / Lint Code",
      "CI Pipeline / Test Suite (3.11)",
      "CI Pipeline / Test Suite (3.12)",
      "CI Pipeline / Temporal Workflow Tests",
      "CI Pipeline / CLI Integration Tests",
      "CI Pipeline / Security Scan",
      "CI Pipeline / Validate Docker Build",
      "CI Pipeline / Validate Kubernetes Manifests",
      "CI Pipeline / All CI Checks",
      "CodeQL Security Analysis / Analyze Code (python)",
      "Docker Build & Push / Build Docker Images"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": $REQUIRED_APPROVALS,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false,
  "required_linear_history": false,
  "required_signatures": true
}
EOF
    elif [ "$BRANCH" == "develop" ]; then
        cat > protection_rules.json << EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "CI Pipeline / Lint Code",
      "CI Pipeline / Test Suite (3.11)",
      "CI Pipeline / Security Scan"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": $REQUIRED_APPROVALS,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": false,
  "lock_branch": false,
  "allow_fork_syncing": false,
  "required_linear_history": false,
  "required_signatures": false
}
EOF
    fi
    
    # Apply branch protection using GitHub API
    if gh api \
        --method PUT \
        -H "Accept: application/vnd.github+json" \
        "/repos/$REPO/branches/$BRANCH/protection" \
        --input protection_rules.json > /dev/null 2>&1; then
        print_success "Branch protection configured for $BRANCH"
    else
        print_warning "Failed to configure branch protection for $BRANCH (branch might not exist yet)"
    fi
    
    rm -f protection_rules.json
}

# Function to create environment
create_environment() {
    local ENV_NAME=$1
    local REVIEWERS=$2
    local WAIT_TIMER=$3
    local DEPLOYMENT_BRANCH=$4
    
    print_status "Creating environment: $ENV_NAME"
    
    # Prepare environment configuration
    cat > env_config.json << EOF
{
  "wait_timer": $WAIT_TIMER,
  "reviewers": $REVIEWERS,
  "deployment_branch_policy": {
    "protected_branches": false,
    "custom_branch_policies": true,
    "custom_branches": [$DEPLOYMENT_BRANCH]
  }
}
EOF
    
    # Create or update environment
    if gh api \
        --method PUT \
        -H "Accept: application/vnd.github+json" \
        "/repos/$REPO/environments/$ENV_NAME" \
        --input env_config.json > /dev/null 2>&1; then
        print_success "Environment $ENV_NAME configured"
    else
        print_warning "Failed to configure environment $ENV_NAME"
    fi
    
    rm -f env_config.json
}

# Main execution
echo "================================================"
echo "GitHub Repository Security Configuration Script"
echo "Repository: $REPO"
echo "================================================"
echo

# Step 1: Configure branch protection for main
print_status "Step 1: Configuring main branch protection..."
configure_branch_protection "main" true 2

# Step 2: Configure branch protection for develop
print_status "Step 2: Configuring develop branch protection..."
configure_branch_protection "develop" true 1

# Step 3: Create staging environment
print_status "Step 3: Creating staging environment..."
create_environment "staging" "[]" 0 '"develop"'

# Step 4: Create production environment
print_status "Step 4: Creating production environment..."
create_environment "production" "[]" 300 '"main", "refs/tags/v*"'

# Step 5: Enable security features
print_status "Step 5: Enabling security features..."

# Enable vulnerability alerts
if gh api \
    --method PUT \
    -H "Accept: application/vnd.github+json" \
    "/repos/$REPO/vulnerability-alerts" 2>/dev/null; then
    print_success "Vulnerability alerts enabled"
else
    print_warning "Could not enable vulnerability alerts (might already be enabled)"
fi

# Enable automated security fixes
if gh api \
    --method PUT \
    -H "Accept: application/vnd.github+json" \
    "/repos/$REPO/automated-security-fixes" 2>/dev/null; then
    print_success "Automated security fixes enabled"
else
    print_warning "Could not enable automated security fixes (might already be enabled)"
fi

# Step 6: Configure Dependabot
print_status "Step 6: Checking Dependabot configuration..."
if [ ! -f ".github/dependabot.yml" ]; then
    print_warning "Dependabot configuration not found. Creating default configuration..."
    mkdir -p .github
    cat > .github/dependabot.yml << 'EOF'
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "04:00"
    open-pull-requests-limit: 5
    reviewers:
      - "security-team"
    labels:
      - "dependencies"
      - "security"
    commit-message:
      prefix: "chore"
      include: "scope"
  
  - package-ecosystem: "docker"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "04:00"
    open-pull-requests-limit: 3
    reviewers:
      - "devops-team"
    labels:
      - "docker"
      - "dependencies"
  
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
      day: "monday"
      time: "04:00"
    open-pull-requests-limit: 3
    labels:
      - "ci/cd"
      - "dependencies"
EOF
    print_success "Dependabot configuration created"
else
    print_success "Dependabot configuration already exists"
fi

# Step 7: Verify required secrets
print_status "Step 7: Verifying required secrets..."
REQUIRED_SECRETS=(
    "GCP_PROJECT_ID"
    "GCP_SA_KEY"
    "GKE_CLUSTER_NAME"
    "GKE_ZONE"
    "CODECOV_TOKEN"
    "PYPI_API_TOKEN"
    "SLACK_WEBHOOK_URL"
)

MISSING_SECRETS=()
for SECRET in "${REQUIRED_SECRETS[@]}"; do
    if gh secret list | grep -q "^$SECRET"; then
        print_success "Secret $SECRET is configured"
    else
        print_warning "Secret $SECRET is NOT configured"
        MISSING_SECRETS+=("$SECRET")
    fi
done

echo
echo "================================================"
echo "Configuration Summary"
echo "================================================"

if [ ${#MISSING_SECRETS[@]} -eq 0 ]; then
    print_success "All required secrets are configured!"
else
    print_warning "The following secrets need to be configured:"
    for SECRET in "${MISSING_SECRETS[@]}"; do
        echo "  - $SECRET"
    done
    echo
    echo "To add a secret, use:"
    echo "  gh secret set SECRET_NAME"
    echo "Or go to: https://github.com/$REPO/settings/secrets/actions"
fi

echo
print_success "GitHub repository security configuration completed!"
echo
echo "Next steps:"
echo "1. Review and configure the missing secrets listed above"
echo "2. Test the branch protection by creating a test PR"
echo "3. Verify CI/CD workflows are running correctly"
echo "4. Review the generated Dependabot configuration if created"
echo
echo "For detailed instructions, see: docs/ci-cd-security-configuration.md"