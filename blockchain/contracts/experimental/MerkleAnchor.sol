// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title MerkleAnchor (experimental, used only for the gas comparison in the paper)
 * @notice Anchors one Merkle root per batch of commitments instead of one
 *         entry per transaction. Not wired into the app.
 */
contract MerkleAnchor {
    struct Batch { bytes32 root; uint32 count; uint64 timestamp; }
    mapping(address => Batch[]) private batches;
    event RootAdded(address indexed user, uint256 indexed index, bytes32 root, uint32 count);

    function addRoot(bytes32 root, uint32 count) external returns (uint256 index) {
        index = batches[msg.sender].length;
        batches[msg.sender].push(Batch(root, count, uint64(block.timestamp)));
        emit RootAdded(msg.sender, index, root, count);
    }

    function getBatch(address user, uint256 index) external view returns (Batch memory) { return batches[user][index]; }
}
