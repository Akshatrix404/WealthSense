# backend

Minimal FastAPI bridge between the WealthSense Android app and the
deployed `TransactionLedger` smart contract. One endpoint, `POST /anchor`.

**Why this exists:** Android cannot safely hold a private key or manage
gas/nonce logic directly — a signing key shipped inside an APK is
extractable by anyone with the file. This backend holds the *one*
server-side signing key and does all chain interaction on the app's
behalf; the app never sees a private key.

## Setup

```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: RPC_URL, PRIVATE_KEY (testnet-only wallet), CONTRACT_ADDRESS
# (from ../blockchain's deploy output)
```

The ABI in `abi/TransactionLedger.json` is copied from
`../blockchain/artifacts/contracts/TransactionLedger.sol/TransactionLedger.json`
after compiling the contract — re-copy it if the contract changes.

## Run

```bash
./venv/bin/uvicorn main:app --reload --port 8000
```

## API (v2: commitments only)

The phone computes an HMAC-SHA256 commitment with a key held in the Android
Keystore (`commitment.py` is the reference implementation, `CommitmentSigner.kt`
the app side). The bridge never receives the amount, category, merchant or time.

```bash
curl -X POST http://127.0.0.1:8000/anchor \
  -H "Content-Type: application/json" \
  -d '{"commitment": "0xc7f1ca2643e8c475d4d47cd47fbb831357de0178a834d56d797e1eed91f973d9"}'
```

Response fields: `tx_hash`, `explorer_url`, `contract_tx_hash` (the commitment, echoed back).

Tested end-to-end against a local Hardhat node (not a public testnet): the bridge
returned 200, `getLedger` returned the same commitment, and the transaction used
90,505 gas for the first entry. Requests with a malformed commitment or with the
old `amount/category/timestamp` body are rejected with HTTP 422.

Run the unit tests for the commitment scheme with `pytest test_commitment.py`.

**Why v2:** the v1 scheme hashed `amount|category|timestamp` without a secret and stored the
plaintext amount and category on chain. An exhaustive search over a plausible amount, category
and time space recovered a target in about 80 s on one CPU core (see the paper's blockchain section).

## Status

- [x] `/anchor` endpoint written and verified end-to-end against a real
      deployed contract (local Hardhat node standing in for Amoy)
- [x] Independently re-verified on-chain, not just trusting the HTTP response
- [ ] Verified against the live Amoy testnet (needs a funded wallet + the
      real deployment from `../blockchain/`)
