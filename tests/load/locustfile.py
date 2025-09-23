"""
Load testing configuration for Research Platform using Locust.

Usage:
    locust -f tests/load/locustfile.py --host=http://localhost:8000
    
    Or with specific parameters:
    locust -f tests/load/locustfile.py \
        --host=http://localhost:8000 \
        --users=100 \
        --spawn-rate=10 \
        --run-time=5m
"""

import random
import uuid
from datetime import datetime

from locust import HttpUser, between, events, task
from locust.exception import RescheduleTask


class ResearchPlatformUser(HttpUser):
    """Simulates a user of the Research Platform."""

    wait_time = between(1, 3)  # Wait 1-3 seconds between tasks

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = None
        self.access_token = None
        self.project_ids = []
        self.email = f"loadtest_{uuid.uuid4().hex[:8]}@example.com"
        self.password = "LoadTest123!@#"

    def on_start(self):
        """Called when a user starts. Register and login."""
        self.register_and_login()

    def on_stop(self):
        """Called when a user stops."""
        # Cleanup if needed
        pass

    def register_and_login(self):
        """Register a new user and login to get access token."""
        # Register
        register_data = {
            "email": self.email,
            "username": f"user_{uuid.uuid4().hex[:8]}",
            "password": self.password,
            "full_name": "Load Test User",
        }

        with self.client.post(
            "/api/v1/auth/register", json=register_data, catch_response=True
        ) as response:
            if response.status_code == 201:
                data = response.json()
                self.user_id = data["user"]["id"]
                self.access_token = data["access_token"]
                response.success()
            else:
                response.failure(f"Registration failed: {response.text}")
                raise RescheduleTask()

    @task(3)
    def list_projects(self):
        """List user's research projects."""
        if not self.access_token:
            return

        with self.client.get(
            "/api/v1/projects", headers=self._auth_headers(), catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed to list projects: {response.status_code}")

    @task(2)
    def create_project(self):
        """Create a new research project."""
        if not self.access_token:
            return

        project_data = {
            "title": f"Load Test Project {datetime.now().isoformat()}",
            "description": "Automated load testing project",
            "query": {
                "text": f"Research question about {random.choice(['AI', 'ML', 'Ethics', 'Biology'])}",
                "domains": random.sample(
                    ["AI", "ML", "Ethics", "Biology", "Physics"], k=2
                ),
                "depth_level": random.choice(
                    ["basic", "intermediate", "comprehensive"]
                ),
            },
        }

        with self.client.post(
            "/api/v1/projects",
            json=project_data,
            headers=self._auth_headers(),
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                project = response.json()
                self.project_ids.append(project["id"])
                response.success()
            else:
                response.failure(f"Failed to create project: {response.status_code}")

    @task(4)
    def get_project_details(self):
        """Get details of a specific project."""
        if not self.access_token or not self.project_ids:
            return

        project_id = random.choice(self.project_ids)

        with self.client.get(
            f"/api/v1/projects/{project_id}",
            headers=self._auth_headers(),
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed to get project: {response.status_code}")

    @task(2)
    def get_project_status(self):
        """Check project execution status."""
        if not self.access_token or not self.project_ids:
            return

        project_id = random.choice(self.project_ids)

        with self.client.get(
            f"/api/v1/projects/{project_id}/status",
            headers=self._auth_headers(),
            catch_response=True,
        ) as response:
            if response.status_code in [200, 202]:
                response.success()
            else:
                response.failure(f"Failed to get status: {response.status_code}")

    @task(1)
    def start_project_workflow(self):
        """Start research workflow for a project."""
        if not self.access_token or not self.project_ids:
            return

        # Only start workflow for projects not yet started
        project_id = random.choice(self.project_ids)

        with self.client.post(
            f"/api/v1/projects/{project_id}/start",
            headers=self._auth_headers(),
            catch_response=True,
        ) as response:
            if response.status_code in [200, 202]:
                response.success()
            elif response.status_code == 409:
                # Already started, that's OK
                response.success()
            else:
                response.failure(f"Failed to start workflow: {response.status_code}")

    @task(3)
    def get_project_results(self):
        """Get research results for a project."""
        if not self.access_token or not self.project_ids:
            return

        project_id = random.choice(self.project_ids)

        with self.client.get(
            f"/api/v1/projects/{project_id}/results",
            headers=self._auth_headers(),
            catch_response=True,
        ) as response:
            if response.status_code in [200, 202, 204]:
                response.success()
            else:
                response.failure(f"Failed to get results: {response.status_code}")

    @task(1)
    def update_profile(self):
        """Update user profile."""
        if not self.access_token:
            return

        profile_data = {"full_name": f"Updated User {random.randint(1, 1000)}"}

        with self.client.patch(
            "/api/v1/auth/me",
            json=profile_data,
            headers=self._auth_headers(),
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Failed to update profile: {response.status_code}")

    @task(1)
    def health_check(self):
        """Perform health check."""
        with self.client.get("/health", catch_response=True) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")

    def _auth_headers(self):
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }


class AdminUser(HttpUser):
    """Simulates an admin user with different behavior patterns."""

    wait_time = between(2, 5)  # Admins operate more slowly

    def on_start(self):
        """Admin login."""
        login_data = {"email": "admin@example.com", "password": "AdminPass123!@#"}

        response = self.client.post("/api/v1/auth/login", json=login_data)
        if response.status_code == 200:
            self.access_token = response.json()["access_token"]
        else:
            raise Exception("Admin login failed")

    @task(3)
    def list_all_users(self):
        """List all users (admin only)."""
        self.client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )

    @task(2)
    def view_system_stats(self):
        """View system statistics."""
        self.client.get(
            "/api/v1/admin/stats",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )

    @task(1)
    def review_audit_logs(self):
        """Review audit logs."""
        self.client.get(
            "/api/v1/admin/audit-logs",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )


class WebSocketUser(HttpUser):
    """Simulates WebSocket connections for real-time updates."""

    wait_time = between(5, 10)

    def on_start(self):
        """Establish WebSocket connection."""
        # Note: Locust doesn't natively support WebSocket
        # This is a placeholder for WebSocket-like behavior
        pass

    @task
    def simulate_websocket_ping(self):
        """Simulate WebSocket ping/pong."""
        self.client.get("/ws/ping")


# Event handlers for reporting
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    print(f"Load test starting with {environment.parsed_options.num_users} users")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops."""
    print("\nLoad test completed!")
    print(f"Total requests: {environment.stats.total.num_requests}")
    print(f"Failure rate: {environment.stats.total.fail_ratio:.2%}")
    print(f"Average response time: {environment.stats.total.avg_response_time:.2f}ms")
    print(f"RPS: {environment.stats.total.current_rps:.2f}")


# Custom failure handler
@events.request.add_listener
def on_request(
    request_type,
    name,
    response_time,
    response_length,
    response,
    context,
    exception,
    **kwargs,
):
    """Custom request handler for detailed logging."""
    if exception:
        print(f"Request failed: {name} - {exception}")
    elif response and response.status_code >= 500:
        print(f"Server error: {name} - Status {response.status_code}")


# Load test scenarios
class QuickLoadTest(ResearchPlatformUser):
    """Quick load test with faster operations."""

    wait_time = between(0.5, 1.5)


class SustainedLoadTest(ResearchPlatformUser):
    """Sustained load test for endurance testing."""

    wait_time = between(3, 7)


class SpikeLoadTest(ResearchPlatformUser):
    """Spike load test with sudden bursts."""

    wait_time = between(0.1, 0.5)
