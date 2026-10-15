require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

// RPC URL and private key are loaded from .env -- NEVER hardcoded here.
// See .env.example for the required variable names. The private key
// used for AMOY_PRIVATE_KEY must be a TESTNET-ONLY wallet funded with
// free Amoy test MATIC from a faucet -- never a wallet holding real funds.
const AMOY_RPC_URL = process.env.AMOY_RPC_URL || "";
const AMOY_PRIVATE_KEY = process.env.AMOY_PRIVATE_KEY || "";

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
    },
  },
  networks: {
    hardhat: {
      // Local, ephemeral network used for `npx hardhat test` -- no real
      // network, no funds, no secrets needed. This is what the unit
      // tests in test/TransactionLedger.test.js actually run against.
    },
    localhost: {
      // A persistent local node (`npx hardhat node`), reachable by
      // external tools like the FastAPI backend for local integration
      // testing without touching the real Amoy network.
      url: "http://127.0.0.1:8545",
    },
    amoy: {
      url: AMOY_RPC_URL,
      // Only include the account if a private key is actually configured,
      // so `npx hardhat test` (which doesn't need it) doesn't fail on a
      // missing/malformed .env in CI or on a fresh clone.
      accounts: AMOY_PRIVATE_KEY ? [AMOY_PRIVATE_KEY] : [],
      chainId: 80002, // Polygon Amoy testnet
    },
  },
  etherscan: {
    // Optional: enables `npx hardhat verify` against Polygonscan's Amoy
    // explorer, if you set POLYGONSCAN_API_KEY in .env.
    apiKey: {
      amoy: process.env.POLYGONSCAN_API_KEY || "",
    },
    customChains: [
      {
        network: "amoy",
        chainId: 80002,
        urls: {
          apiURL: "https://api-amoy.polygonscan.com/api",
          browserURL: "https://amoy.polygonscan.com",
        },
      },
    ],
  },
};
