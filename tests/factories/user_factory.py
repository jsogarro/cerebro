"""
User factory for generating test user data.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from factory import Factory, LazyAttribute, LazyFunction, Sequence
from factory.fuzzy import FuzzyChoice, FuzzyDateTime
from faker import Faker

from src.auth.password_service import PasswordService
from src.models.db.api_key import APIKey
from src.models.db.session import Session
from src.models.db.user import User

fake = Faker()
password_service = PasswordService()


class UserFactory(Factory):
    """Factory for creating test users."""

    class Meta:
        model = User

    id = LazyFunction(lambda: str(uuid.uuid4()))
    email = Sequence(lambda n: f"user{n}@example.com")
    username = Sequence(lambda n: f"user{n}")
    full_name = LazyAttribute(lambda o: fake.name())
    hashed_password = LazyAttribute(
        lambda o: password_service.hash_password("Password123!@#")
    )
    role = FuzzyChoice(["admin", "researcher", "viewer"])
    is_active = True
    is_verified = True
    is_superuser = False
    created_at = FuzzyDateTime(
        start_dt=datetime.utcnow() - timedelta(days=30), end_dt=datetime.utcnow()
    )
    updated_at = LazyAttribute(lambda o: o.created_at + timedelta(hours=1))
    last_login = LazyAttribute(lambda o: o.created_at + timedelta(days=1))

    @classmethod
    def create_admin(cls, **kwargs) -> User:
        """Create an admin user."""
        defaults = {
            "role": "admin",
            "is_superuser": True,
            "username": f"admin_{fake.user_name()}",
            "email": f"admin_{fake.email()}",
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_researcher(cls, **kwargs) -> User:
        """Create a researcher user."""
        defaults = {
            "role": "researcher",
            "username": f"researcher_{fake.user_name()}",
            "email": f"researcher_{fake.email()}",
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_viewer(cls, **kwargs) -> User:
        """Create a viewer user."""
        defaults = {
            "role": "viewer",
            "username": f"viewer_{fake.user_name()}",
            "email": f"viewer_{fake.email()}",
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_unverified(cls, **kwargs) -> User:
        """Create an unverified user."""
        defaults = {
            "is_verified": False,
            "is_active": True,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_inactive(cls, **kwargs) -> User:
        """Create an inactive user."""
        defaults = {
            "is_active": False,
            "is_verified": True,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_with_custom_password(cls, password: str, **kwargs) -> User:
        """Create a user with a custom password."""
        hashed = password_service.hash_password(password)
        return cls(hashed_password=hashed, **kwargs)

    @classmethod
    def create_batch_with_roles(cls, count: int = 10) -> list[User]:
        """Create a batch of users with different roles."""
        users = []
        roles = ["admin", "researcher", "viewer"]

        for i in range(count):
            role = roles[i % len(roles)]
            user = cls(
                role=role,
                username=f"{role}_user_{i}",
                email=f"{role}_{i}@example.com",
            )
            users.append(user)

        return users


class SessionFactory(Factory):
    """Factory for creating test sessions."""

    class Meta:
        model = Session

    id = LazyFunction(lambda: str(uuid.uuid4()))
    user_id = LazyFunction(lambda: str(uuid.uuid4()))
    token = LazyFunction(lambda: fake.sha256())
    refresh_token = LazyFunction(lambda: fake.sha256())
    expires_at = LazyFunction(lambda: datetime.utcnow() + timedelta(hours=24))
    refresh_expires_at = LazyFunction(lambda: datetime.utcnow() + timedelta(days=7))
    ip_address = LazyFunction(fake.ipv4)
    user_agent = LazyFunction(fake.user_agent)
    is_active = True
    created_at = LazyFunction(datetime.utcnow)
    last_activity = LazyFunction(datetime.utcnow)

    @classmethod
    def create_expired(cls, **kwargs) -> Session:
        """Create an expired session."""
        defaults = {
            "expires_at": datetime.utcnow() - timedelta(hours=1),
            "refresh_expires_at": datetime.utcnow() - timedelta(hours=1),
            "is_active": False,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_for_user(cls, user: User, **kwargs) -> Session:
        """Create a session for a specific user."""
        defaults = {"user_id": user.id}
        defaults.update(kwargs)
        return cls(**defaults)


class APIKeyFactory(Factory):
    """Factory for creating test API keys."""

    class Meta:
        model = APIKey

    id = LazyFunction(lambda: str(uuid.uuid4()))
    user_id = LazyFunction(lambda: str(uuid.uuid4()))
    name = LazyAttribute(lambda o: f"API Key {fake.word()}")
    key_hash = LazyFunction(lambda: fake.sha256())
    key_prefix = LazyAttribute(lambda o: o.key_hash[:8])
    scopes = LazyFunction(
        lambda: fake.random_elements(
            elements=["read", "write", "delete", "admin"], length=2, unique=True
        )
    )
    expires_at = LazyFunction(lambda: datetime.utcnow() + timedelta(days=90))
    is_active = True
    last_used_at = None
    created_at = LazyFunction(datetime.utcnow)

    @classmethod
    def create_expired(cls, **kwargs) -> APIKey:
        """Create an expired API key."""
        defaults = {
            "expires_at": datetime.utcnow() - timedelta(days=1),
            "is_active": False,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_with_full_access(cls, **kwargs) -> APIKey:
        """Create an API key with full access."""
        defaults = {
            "scopes": ["read", "write", "delete", "admin"],
        }
        defaults.update(kwargs)
        return cls(**defaults)

    @classmethod
    def create_readonly(cls, **kwargs) -> APIKey:
        """Create a read-only API key."""
        defaults = {
            "scopes": ["read"],
            "name": f"Read-only Key {fake.word()}",
        }
        defaults.update(kwargs)
        return cls(**defaults)


class TestUserGenerator:
    """Generate realistic test user scenarios."""

    @staticmethod
    def create_user_with_history(
        num_sessions: int = 5, num_api_keys: int = 2
    ) -> dict[str, Any]:
        """Create a user with session and API key history."""
        user = UserFactory()

        # Create sessions
        sessions = []
        for i in range(num_sessions):
            session = SessionFactory.create_for_user(
                user,
                created_at=user.created_at + timedelta(days=i),
                is_active=(i == num_sessions - 1),  # Only last session is active
            )
            sessions.append(session)

        # Create API keys
        api_keys = []
        for i in range(num_api_keys):
            api_key = APIKeyFactory(
                user_id=user.id, created_at=user.created_at + timedelta(days=i * 30)
            )
            api_keys.append(api_key)

        return {
            "user": user,
            "sessions": sessions,
            "api_keys": api_keys,
        }

    @staticmethod
    def create_organization_users(org_name: str = "Test Org") -> list[User]:
        """Create users for an organization."""
        users = []

        # Create admin
        admin = UserFactory.create_admin(
            username=f"{org_name.lower().replace(' ', '_')}_admin",
            email=f"admin@{org_name.lower().replace(' ', '')}.com",
            full_name=f"{org_name} Administrator",
        )
        users.append(admin)

        # Create researchers
        for i in range(3):
            researcher = UserFactory.create_researcher(
                username=f"{org_name.lower().replace(' ', '_')}_researcher_{i}",
                email=f"researcher{i}@{org_name.lower().replace(' ', '')}.com",
                full_name=f"{org_name} Researcher {i+1}",
            )
            users.append(researcher)

        # Create viewers
        for i in range(2):
            viewer = UserFactory.create_viewer(
                username=f"{org_name.lower().replace(' ', '_')}_viewer_{i}",
                email=f"viewer{i}@{org_name.lower().replace(' ', '')}.com",
                full_name=f"{org_name} Viewer {i+1}",
            )
            users.append(viewer)

        return users

    @staticmethod
    def create_test_authentication_scenarios() -> dict[str, User]:
        """Create users for various authentication test scenarios."""
        return {
            "valid_user": UserFactory(
                username="valid_user",
                email="valid@example.com",
                is_active=True,
                is_verified=True,
            ),
            "unverified_user": UserFactory.create_unverified(
                username="unverified_user", email="unverified@example.com"
            ),
            "inactive_user": UserFactory.create_inactive(
                username="inactive_user", email="inactive@example.com"
            ),
            "admin_user": UserFactory.create_admin(
                username="admin_user", email="admin@example.com"
            ),
            "researcher_user": UserFactory.create_researcher(
                username="researcher_user", email="researcher@example.com"
            ),
            "viewer_user": UserFactory.create_viewer(
                username="viewer_user", email="viewer@example.com"
            ),
        }
