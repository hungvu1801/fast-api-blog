# Writes test demos for synchonous apps

from fastapi import FastAPI
from fastapi.testclient import TestClient

demo_app = FastAPI()


@demo_app.get("/")
def demo_home():
    return {"message": "Hello"}


client = TestClient(demo_app)


def test_homepage():
    responses = client.get("/")
    assert responses.status_code == 200
