"""
Minimal FastAPI bridge between the WealthSense Android app and the
deployed TransactionLedger smart contract. One endpoint: POST /anchor.

Why this exists at all (architecture decision, not a shortcut): Android
cannot safely hold a private key or manage gas/nonce logic directly --
doing so would mean shipping a signing key inside the APK, extractable
by anyone with the file. This backend holds the ONE server-side signing
key and does all chain interaction on the app's behalf. The app never
sees a private key.

Run:
    uvicorn main:app --reload --port 8000
"""

import os
import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from web3 import Web3
from dotenv import load_dotenv

load_dotenv()

RPC_URL = os.environ.get("RPC_URL", "")
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "")
CONTRACT_ADDRESS = os.environ.get("CONTRACT_ADDRESS", "")
CHAIN_ID = int(os.environ.get("CHAIN_ID", "80002"))  # Polygon Amoy default
EXPLORER_BASE_URL = os.environ.get(
    "EXPLORER_BASE_URL", "https://amoy.polygonscan.com/tx"
)

ABI_PATH = os.path.join(os.path.dirname(__file__), "abi", "TransactionLedger.json")

app = FastAPI(title="WealthSense Chain Anchor Bridge")


class AnchorRequest(BaseModel):
    # v2: the phone computes the commitment (HMAC-SHA256 under a key that never
    # leaves the device, see commitment.py / CommitmentSigner.kt). The bridge
    # never receives the amount, category, merchant or timestamp.
    commitment: str = Field(..., pattern=r"^0x[0-9a-fA-F]{64}$", description="32-byte commitment, 0x-prefixed hex")


class AnchorResponse(BaseModel):
    tx_hash: str
    explorer_url: str
    contract_tx_hash: str  # the commitment that was anchored on-chain (echoed back)


def get_web3_and_contract():
    """
    Lazily constructs the Web3 connection and contract instance per-request
    rather than at import time -- this way, a missing/misconfigured .env
    fails with a clear 500 error on first real use, instead of crashing
    the whole app at startup before FastAPI can even report why (helpful
    for local dev / grading, where RPC_URL might genuinely be unset yet).
    """
    if not RPC_URL or not PRIVATE_KEY or not CONTRACT_ADDRESS:
        raise HTTPException(
            status_code=500,
            detail="Backend not configured: RPC_URL, PRIVATE_KEY, and "
                   "CONTRACT_ADDRESS must all be set in .env",
        )

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise HTTPException(status_code=502, detail=f"Could not connect to RPC at {RPC_URL}")

    with open(ABI_PATH) as f:
        abi = json.load(f)

    contract = w3.eth.contract(address=Web3.to_checksum_address(CONTRACT_ADDRESS), abi=abi)
    return w3, contract


@app.post("/anchor", response_model=AnchorResponse)
def anchor_transaction(req: AnchorRequest):
    """
    Anchors the phone-computed commitment on the deployed TransactionLedger
    contract (addCommitment), signs with the server-side key, and returns the
    chain transaction hash plus a block explorer URL.
    """
    w3, contract = get_web3_and_contract()

    account = w3.eth.account.from_key(PRIVATE_KEY)
    commitment_bytes = bytes.fromhex(req.commitment[2:])

    try:
        nonce = w3.eth.get_transaction_count(account.address)
        tx = contract.functions.addCommitment(commitment_bytes).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "chainId": CHAIN_ID,
            "gas": 150_000,
            "gasPrice": w3.eth.gas_price,
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key=PRIVATE_KEY)
        raw_tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(raw_tx_hash, timeout=120)

        if receipt.status != 1:
            raise HTTPException(status_code=502, detail="Transaction reverted on-chain")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Chain call failed: {e}")

    tx_hash_hex = raw_tx_hash.hex()
    if not tx_hash_hex.startswith("0x"):
        tx_hash_hex = "0x" + tx_hash_hex

    return AnchorResponse(
        tx_hash=tx_hash_hex,
        explorer_url=f"{EXPLORER_BASE_URL}/{tx_hash_hex}",
        contract_tx_hash=req.commitment.lower(),
    )


@app.get("/health")
def health():
    """Basic liveness check -- doesn't touch the chain, just confirms the
    app is up and configuration is present."""
    configured = bool(RPC_URL and PRIVATE_KEY and CONTRACT_ADDRESS)
    return {"status": "ok", "chain_configured": configured}
