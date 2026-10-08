"""ASGI entry point for an AWS Lambda function URL."""
from mangum import Mangum
from app.main import app

handler = Mangum(app, lifespan="off")
