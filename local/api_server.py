"""
ローカル開発用 API サーバー。
API Gateway + Lambda の代わりに FastAPI で同じエンドポイントを提供する。
認証なし・DynamoDB Local に接続する。
"""
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

sys.path.insert(0, "/lambda")
import api_handler  # noqa: E402  (env vars must be set before this import)


def _init_tables() -> None:
    import boto3
    ddb = boto3.resource(
        "dynamodb",
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT_URL"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )

    schemas = [
        {
            "TableName": os.environ["EXPERIMENT_TABLE"],
            "KeySchema": [
                {"AttributeName": "experiment_id", "KeyType": "HASH"},
                {"AttributeName": "started_at", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "experiment_id", "AttributeType": "S"},
                {"AttributeName": "started_at", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": os.environ["SLO_TABLE"],
            "KeySchema": [{"AttributeName": "service_name", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "service_name", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]
    for schema in schemas:
        try:
            ddb.create_table(**schema)
            print(f"[init] created table: {schema['TableName']}")
        except ddb.meta.client.exceptions.ResourceInUseException:
            print(f"[init] table exists: {schema['TableName']}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_tables()
    yield


app = FastAPI(title="Chaos Platform API (local dev)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


def _to_response(result: dict) -> JSONResponse:
    import json
    return JSONResponse(
        content=json.loads(result["body"]),
        status_code=result["statusCode"],
    )


@app.get("/experiments")
def list_experiments(
    service: str | None = Query(default=None),
    limit: int = Query(default=20),
):
    return _to_response(api_handler.list_experiments(service, limit))


@app.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str):
    return _to_response(api_handler.get_experiment(experiment_id))


@app.post("/experiments")
async def start_experiment(request: Request):
    body = await request.json()
    return _to_response(api_handler.start_experiment(body))


@app.delete("/experiments/{experiment_id}")
async def stop_experiment(experiment_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = body.get("reason", "manual") if body else "manual"
    return _to_response(api_handler.stop_experiment(experiment_id, reason))
