// Gas comparison: one commitment per transaction vs one Merkle root per batch.
// Run: npx hardhat --config hardhat.offline.config.js run scripts/gas_experiment.js
const hre = require("hardhat");
const { ethers } = hre;
async function main() {
  const [s] = await ethers.getSigners();
  const L = await (await ethers.getContractFactory("TransactionLedger")).deploy(); await L.waitForDeployment();
  const M = await (await ethers.getContractFactory("MerkleAnchor")).deploy(); await M.waitForDeployment();
  const dep = async (c) => (await c.deploymentTransaction().wait()).gasUsed;
  const out = { deploy_ledger: Number(await dep(L)), deploy_merkle: Number(await dep(M)), per_tx: [], root: [] };
  for (let i = 0; i < 20; i++) {
    const r = await (await L.addCommitment(ethers.hexlify(ethers.randomBytes(32)))).wait();
    out.per_tx.push(Number(r.gasUsed));
  }
  for (const n of [10, 50, 100]) {
    const r = await (await M.addRoot(ethers.hexlify(ethers.randomBytes(32)), n)).wait();
    out.root.push({ batch: n, gas: Number(r.gasUsed) });
  }
  // second root (warm array slot) for comparison
  const r2 = await (await M.addRoot(ethers.hexlify(ethers.randomBytes(32)), 10)).wait(); out.second_root_gas = Number(r2.gasUsed);
  console.log(JSON.stringify(out));
}
main().catch((e) => { console.error(e); process.exitCode = 1; });
