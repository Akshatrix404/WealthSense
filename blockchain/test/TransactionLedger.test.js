const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("TransactionLedger (v2, commitments only)", function () {
  async function deployFixture() {
    const [owner, otherUser] = await ethers.getSigners();
    const F = await ethers.getContractFactory("TransactionLedger");
    const ledger = await F.deploy();
    await ledger.waitForDeployment();
    return { ledger, owner, otherUser };
  }

  it("1. deploys successfully with a valid contract address", async function () {
    const { ledger } = await deployFixture();
    const address = await ledger.getAddress();
    expect(address).to.properAddress;
    expect(address).to.not.equal(ethers.ZeroAddress);
  });

  it("2. stores a commitment and emits CommitmentAdded", async function () {
    const { ledger, owner } = await deployFixture();
    const c = ethers.hexlify(ethers.randomBytes(32));
    await expect(ledger.connect(owner).addCommitment(c))
      .to.emit(ledger, "CommitmentAdded")
      .withArgs(owner.address, 0, c, (v) => typeof v === "bigint");
    const stored = await ledger.getLedger(owner.address);
    expect(stored.length).to.equal(1);
    expect(stored[0].commitment).to.equal(c);
    expect(await ledger.getLedgerLength(owner.address)).to.equal(1);
  });

  it("3. keeps ledgers separate per address and preserves order", async function () {
    const { ledger, owner, otherUser } = await deployFixture();
    const a = ethers.hexlify(ethers.randomBytes(32)), b = ethers.hexlify(ethers.randomBytes(32));
    await ledger.connect(owner).addCommitment(a);
    await ledger.connect(owner).addCommitment(b);
    const l = await ledger.getLedger(owner.address);
    expect(l[0].commitment).to.equal(a);
    expect(l[1].commitment).to.equal(b);
    expect((await ledger.getLedger(otherUser.address)).length).to.equal(0);
  });

  it("4. contract storage and event contain no plaintext transaction fields", async function () {
    const { ledger, owner } = await deployFixture();
    const c = ethers.hexlify(ethers.randomBytes(32));
    const tx = await ledger.connect(owner).addCommitment(c);
    const rcpt = await tx.wait();
    const data = (rcpt.logs[0].data + rcpt.logs[0].topics.join("")).toLowerCase();
    expect(data).to.not.include(ethers.hexlify(ethers.toUtf8Bytes("Food")).slice(2));
  });
});
