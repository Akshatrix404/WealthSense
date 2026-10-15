// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title TransactionLedger (v2: commitments only)
 * @notice Anchors an opaque 32-byte commitment per WealthSense transaction.
 *
 * Version 2 changes (privacy and brute-force resistance):
 *   - v1 stored the plaintext amount and category next to an unsalted hash
 *     of "amount|category|timestamp". Both are low-entropy and public on a
 *     public chain, so v1 leaked the very fields it was protecting.
 *   - v2 stores only a commitment c = HMAC-SHA256(K, canonical(tx)), where K
 *     is a 256-bit key that never leaves the phone (Android Keystore). The
 *     contract and the bridge see only c; without K the commitment cannot be
 *     brute-forced from the small space of plausible amounts/categories.
 *   - The block timestamp is the only time information stored on chain.
 *
 * The caller (the bridge account) owns an append-only array of commitments.
 */
contract TransactionLedger {
    struct Entry {
        bytes32 commitment;
        uint256 timestamp;
    }

    mapping(address => Entry[]) private ledger;

    event CommitmentAdded(address indexed user, uint256 indexed index, bytes32 commitment, uint256 timestamp);

    /// @notice Appends a commitment for the caller and returns its index.
    function addCommitment(bytes32 commitment) external returns (uint256 index) {
        index = ledger[msg.sender].length;
        ledger[msg.sender].push(Entry({commitment: commitment, timestamp: block.timestamp}));
        emit CommitmentAdded(msg.sender, index, commitment, block.timestamp);
    }

    function getLedger(address user) external view returns (Entry[] memory) {
        return ledger[user];
    }

    function getLedgerLength(address user) external view returns (uint256) {
        return ledger[user].length;
    }

    function getEntry(address user, uint256 index) external view returns (Entry memory) {
        return ledger[user][index];
    }
}
