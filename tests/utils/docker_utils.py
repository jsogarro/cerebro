"""
Docker utilities for integration testing.
"""

import logging
import os
import time
from contextlib import contextmanager
from typing import Any

import docker

logger = logging.getLogger(__name__)


class DockerTestManager:
    """Manage Docker containers for testing."""

    def __init__(self):
        self.client = docker.from_env()
        self.containers = {}
        self.networks = {}
        self.volumes = {}

    def create_network(self, name: str = "test_network") -> Any:
        """Create a Docker network for testing."""
        try:
            network = self.client.networks.get(name)
            logger.info(f"Using existing network: {name}")
        except docker.errors.NotFound:
            network = self.client.networks.create(name, driver="bridge")
            logger.info(f"Created network: {name}")

        self.networks[name] = network
        return network

    def start_postgres(
        self,
        name: str = "test_postgres",
        port: int = 5433,
        password: str = "test_password",
    ) -> dict[str, Any]:
        """Start a PostgreSQL container for testing."""
        container = self.client.containers.run(
            "postgres:16-alpine",
            name=name,
            environment={
                "POSTGRES_PASSWORD": password,
                "POSTGRES_USER": "test_user",
                "POSTGRES_DB": "test_db",
            },
            ports={"5432/tcp": port},
            detach=True,
            remove=True,
            network=self.networks.get("test_network"),
        )

        self.containers[name] = container

        # Wait for PostgreSQL to be ready
        self._wait_for_postgres(container, password)

        return {
            "container": container,
            "host": "localhost",
            "port": port,
            "user": "test_user",
            "password": password,
            "database": "test_db",
            "url": f"postgresql://test_user:{password}@localhost:{port}/test_db",
        }

    def start_redis(self, name: str = "test_redis", port: int = 6380) -> dict[str, Any]:
        """Start a Redis container for testing."""
        container = self.client.containers.run(
            "redis:7-alpine",
            name=name,
            ports={"6379/tcp": port},
            detach=True,
            remove=True,
            network=self.networks.get("test_network"),
        )

        self.containers[name] = container

        # Wait for Redis to be ready
        self._wait_for_redis(container)

        return {
            "container": container,
            "host": "localhost",
            "port": port,
            "url": f"redis://localhost:{port}/0",
        }

    def start_temporal(
        self, name: str = "test_temporal", port: int = 7234
    ) -> dict[str, Any]:
        """Start a Temporal server for testing."""
        container = self.client.containers.run(
            "temporalio/auto-setup:latest",
            name=name,
            environment={
                "DB": "sqlite",
                "SKIP_SCHEMA_SETUP": "false",
                "SKIP_DEFAULT_NAMESPACE_CREATION": "false",
            },
            ports={"7233/tcp": port},
            detach=True,
            remove=True,
            network=self.networks.get("test_network"),
        )

        self.containers[name] = container

        # Wait for Temporal to be ready
        self._wait_for_temporal(container)

        return {
            "container": container,
            "host": "localhost",
            "port": port,
            "url": f"localhost:{port}",
        }

    def _wait_for_postgres(
        self, container: Any, password: str, max_retries: int = 30, retry_delay: int = 1
    ):
        """Wait for PostgreSQL to be ready."""
        for i in range(max_retries):
            try:
                result = container.exec_run(
                    ["pg_isready", "-U", "test_user", "-d", "test_db"],
                    environment={"PGPASSWORD": password},
                )
                if result.exit_code == 0:
                    logger.info("PostgreSQL is ready")
                    return
            except Exception as e:
                logger.debug(f"Waiting for PostgreSQL: {e}")

            time.sleep(retry_delay)

        raise TimeoutError("PostgreSQL failed to start")

    def _wait_for_redis(
        self, container: Any, max_retries: int = 30, retry_delay: int = 1
    ):
        """Wait for Redis to be ready."""
        for i in range(max_retries):
            try:
                result = container.exec_run(["redis-cli", "ping"])
                if b"PONG" in result.output:
                    logger.info("Redis is ready")
                    return
            except Exception as e:
                logger.debug(f"Waiting for Redis: {e}")

            time.sleep(retry_delay)

        raise TimeoutError("Redis failed to start")

    def _wait_for_temporal(
        self, container: Any, max_retries: int = 60, retry_delay: int = 2
    ):
        """Wait for Temporal to be ready."""
        for i in range(max_retries):
            try:
                result = container.exec_run(
                    ["tctl", "--address", "localhost:7233", "namespace", "list"]
                )
                if result.exit_code == 0:
                    logger.info("Temporal is ready")
                    return
            except Exception as e:
                logger.debug(f"Waiting for Temporal: {e}")

            time.sleep(retry_delay)

        raise TimeoutError("Temporal failed to start")

    def stop_container(self, name: str):
        """Stop and remove a container."""
        if name in self.containers:
            try:
                self.containers[name].stop()
                self.containers[name].remove()
                del self.containers[name]
                logger.info(f"Stopped container: {name}")
            except Exception as e:
                logger.error(f"Error stopping container {name}: {e}")

    def cleanup(self):
        """Clean up all containers, networks, and volumes."""
        # Stop all containers
        for name in list(self.containers.keys()):
            self.stop_container(name)

        # Remove networks
        for name, network in self.networks.items():
            try:
                network.remove()
                logger.info(f"Removed network: {name}")
            except Exception as e:
                logger.error(f"Error removing network {name}: {e}")

        # Remove volumes
        for name, volume in self.volumes.items():
            try:
                volume.remove()
                logger.info(f"Removed volume: {name}")
            except Exception as e:
                logger.error(f"Error removing volume {name}: {e}")

        self.networks.clear()
        self.volumes.clear()


@contextmanager
def docker_test_environment(services: list[str] = ["postgres", "redis", "temporal"]):
    """Context manager for Docker test environment."""
    manager = DockerTestManager()

    try:
        # Create network
        manager.create_network()

        # Start requested services
        services_info = {}

        if "postgres" in services:
            services_info["postgres"] = manager.start_postgres()

        if "redis" in services:
            services_info["redis"] = manager.start_redis()

        if "temporal" in services:
            services_info["temporal"] = manager.start_temporal()

        yield services_info

    finally:
        manager.cleanup()


class DockerComposeManager:
    """Manage Docker Compose for testing."""

    def __init__(self, compose_file: str):
        self.compose_file = compose_file
        self.project_name = "test_" + str(int(time.time()))

    def up(self, services: list[str] | None = None):
        """Start Docker Compose services."""
        cmd = [
            "docker-compose",
            "-f",
            self.compose_file,
            "-p",
            self.project_name,
            "up",
            "-d",
        ]

        if services:
            cmd.extend(services)

        os.system(" ".join(cmd))
        logger.info(f"Started Docker Compose with project: {self.project_name}")

    def down(self, volumes: bool = True):
        """Stop Docker Compose services."""
        cmd = [
            "docker-compose",
            "-f",
            self.compose_file,
            "-p",
            self.project_name,
            "down",
        ]

        if volumes:
            cmd.append("-v")

        os.system(" ".join(cmd))
        logger.info(f"Stopped Docker Compose project: {self.project_name}")

    def logs(self, service: str | None = None, tail: int = 100):
        """Get logs from Docker Compose services."""
        cmd = [
            "docker-compose",
            "-f",
            self.compose_file,
            "-p",
            self.project_name,
            "logs",
            f"--tail={tail}",
        ]

        if service:
            cmd.append(service)

        os.system(" ".join(cmd))

    def exec(self, service: str, command: list[str]):
        """Execute command in a service container."""
        cmd = [
            "docker-compose",
            "-f",
            self.compose_file,
            "-p",
            self.project_name,
            "exec",
            "-T",
            service,
        ] + command

        os.system(" ".join(cmd))

    def wait_for_service(
        self,
        service: str,
        check_command: list[str],
        max_retries: int = 30,
        retry_delay: int = 2,
    ):
        """Wait for a service to be ready."""
        for i in range(max_retries):
            result = os.system(
                f"docker-compose -f {self.compose_file} -p {self.project_name} "
                f"exec -T {service} {' '.join(check_command)} > /dev/null 2>&1"
            )

            if result == 0:
                logger.info(f"Service {service} is ready")
                return

            time.sleep(retry_delay)

        raise TimeoutError(f"Service {service} failed to start")


@contextmanager
def docker_compose_environment(compose_file: str):
    """Context manager for Docker Compose test environment."""
    manager = DockerComposeManager(compose_file)

    try:
        manager.up()

        # Wait for services to be ready
        manager.wait_for_service(
            "postgres", ["pg_isready", "-U", "test_user", "-d", "test_research_db"]
        )
        manager.wait_for_service("redis", ["redis-cli", "ping"])
        manager.wait_for_service(
            "temporal", ["tctl", "--address", "temporal:7233", "namespace", "list"]
        )

        yield manager

    finally:
        manager.down(volumes=True)
