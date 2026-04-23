import pytest
import tempfile
import os


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
def sample_service_tag():
    """Provide a sample service tag for testing."""
    return "ABC1234"


@pytest.fixture
def sample_hostname():
    """Provide a sample hostname for testing."""
    return "server01"


@pytest.fixture
def sample_model():
    """Provide a sample model for testing."""
    return "R660"
