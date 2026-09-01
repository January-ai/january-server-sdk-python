import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request

from januaryai import AsyncJanuary


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    key = os.environ.get("JANUARY_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "Set JANUARY_API_KEY in .env or your environment before running this example."
        )
    async with AsyncJanuary(secret_key=key, max_retries=0) as january:
        app.state.january = january
        yield


app = FastAPI(lifespan=lifespan)


async def require_authenticated_user(
    x_demo_user_id: Annotated[str | None, Header()] = None,
) -> str:
    # LOCAL DEMO ONLY. Replace this with the application's verified session/JWT.
    if not x_demo_user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    return x_demo_user_id


@app.post("/api/january/token")
async def create_january_token(
    request: Request,
    user_id: Annotated[str, Depends(require_authenticated_user)],
) -> dict[str, object]:
    # The caller cannot choose end_user_id or scopes; both are server-controlled.
    try:
        token = await request.app.state.january.create_client_token(
            end_user_id=user_id,
            scopes=["foods:read"],
            ttl_seconds=1800,
        )
        # Relay schema expected by client SDKs: expires_in (seconds) -> expiresIn.
        return {"token": token.token, "expiresIn": token.expires_in}
    except Exception:
        # Never expose or log upstream bodies, credentials, or exception causes.
        raise HTTPException(status_code=502, detail="Unable to mint client token") from None
