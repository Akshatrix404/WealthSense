const hre = require("hardhat");

async function main() {
  console.log(`Deploying TransactionLedger to network: ${hre.network.name} ...`);

  const TransactionLedger = await hre.ethers.getContractFactory("TransactionLedger");
  const ledger = await TransactionLedger.deploy();
  await ledger.waitForDeployment();

  const address = await ledger.getAddress();
  console.log(`\nTransactionLedger deployed to: ${address}`);

  if (hre.network.name === "amoy") {
    console.log(`Polygonscan (Amoy): https://amoy.polygonscan.com/address/${address}`);
    console.log(`\nNext steps:`);
    console.log(`  1. Save this address -- the FastAPI bridge (Prompt 5) needs it.`);
    console.log(`  2. Copy the ABI from artifacts/contracts/TransactionLedger.sol/TransactionLedger.json`);
    console.log(`     into the backend service.`);
    console.log(`  3. Optionally verify the source: npx hardhat verify --network amoy ${address}`);
  } else {
    console.log(`\n(Deployed to a local/non-Amoy network -- this address is not persistent.)`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
