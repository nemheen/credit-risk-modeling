"""
You can create a file called `.env` in the root of the repo, containing your local env vars.
"""
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load environment variables
load_dotenv(verbose=True)


class Settings(BaseSettings):
    """.env variables have priority over following default values"""

    oracle_username: str
    oracle_password: str
    oracle_service: str
    oracle_hostname: str
    oracle_port: str = '1521'
    log_level: str = 'INFO'

    class Config:
        """.env file location"""

        env_file = ".env"


settings = Settings()
