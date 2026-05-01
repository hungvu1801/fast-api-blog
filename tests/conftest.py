###############################################
### Testing strategy
"""
1. We dont want to touch any of the development database. Because tests usually creating/deleting
records in the database. SOOO we want to use another postgres testing database NOT sqlite.
for compatible with real production ones.

2. Transactional rollback pattern
- Create all database tables once at the begining of the test
- Each of the test runs inside the database transaction.
- Then after each of the test complete, we ROLLBACK the transaction which undone the what the test just did
- At the end of the session, we drop all of the database tables
"""

###############################################
import os
from collections.abc import AsyncGenerator

# These enviroment reference always comes first before importing the library
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://bloguser:blogpass@localhost/test_blog"
)

os.environ["S3_BUCKET_NAME"] = "test-bucket"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"

# Dummy S3/AWS Credentials
os.environ["S3_ACCESS_KEY_ID"] = "testing"
os.environ["S3_SECRET_ACCESS_KEY"] = "testing"
os.environ["S3_REGION"] = "us-east-1"

os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from database import Base, get_db
from main import app

# standard way to enable pytest plug in
pytest_plugins = ["anyio"]  # let write async test function


@pytest.fixture(
    scope="session"
)  # scope="session" means runs once in an entire test session rather once per test
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def test_engine():
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        poolclass=NullPool,  # disable connection pool entirely
    )
    return engine


# IMPORTANT NOTE FOR FUTURE ME:
## We must run two functions together with the SESSION SCOPE so everytest runs in the SAME SESSION
## and the event-loop lives on the entire testing session. If the "function scope", NEW event-loop
## would be created with every test, and the test_engine created once in the first test, will be bounded
## to that event-loop and would throws errors if the subsequence event-loop created but never found the engine


@pytest.fixture(scope="session")
async def setup_database(test_engine):
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await test_engine.dispose()


@pytest.fixture
async def db_session(test_engine, setup_database) -> AsyncGenerator[AsyncSession]:
    conn = await test_engine.connect()
    trans = await conn.begin()
    test_async_session = async_sessionmaker(
        bind=conn,  # session tests will be bound and come through one connection, not engine
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    ### "create_savepoint" creates a fake commit. When our app calls session.commit,
    ### the sqlalchemy intercepts that, and the data looks committed to the app code
    ### but nothing happens to the database

    async with test_async_session() as session:
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
            await conn.close()


@pytest.fixture
def mocked_aws():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=os.environ["S3_BUCKET_NAME"])
        yield s3


@pytest.fixture
async def client(
    db_session: AsyncSession,
    mocked_aws,
) -> AsyncGenerator[AsyncClient]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


async def create_test_user(
    client: AsyncClient,
    username: str = "testuser",
    email: str = "test@example.com",
    password: str = "testpassword123",
) -> dict:
    response = await client.post(
        "/api/users",
        json={
            "username": username,
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 201, f"Failed to create user: {response.text}"
    return response.json()


async def login_user(
    client: AsyncClient,
    email: str = "test@example.com",
    password: str = "testpassword123",
) -> str:

    reponse = await client.post(
        "/api/users/token",
        data={
            "email": email,
            "password": password,
        },
    )  # oauth2 requires data, not json
    assert reponse.status_code == 200, f"Failed to login: {reponse.text}"
    return reponse.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
