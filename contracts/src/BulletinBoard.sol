// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.28;

contract BulletinBoard {
    uint256 private constant MAX_PAYLOAD_LENGTH = 16 * 1024 * 1024;
    uint256 private constant SECP256K1_HALF_ORDER = 0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    bytes32 public immutable electionId;
    bytes32 public immutable manifestHash;
    uint64 public immutable batchDeadline;
    uint64 public immutable partialDeadline;

    address[] private _machines;
    address[] private _trustees;
    mapping(address => bool) private _registeredMachines;
    mapping(address => bool) private _registeredTrustees;
    mapping(address => bool) public batchPosted;
    mapping(uint256 => bool) public partialPosted;
    mapping(bytes32 => uint256) private _aggregateDigestVotes;

    bytes32 public snapshotHash;
    bytes32 public pinnedAggregateDigest;

    error InvalidAnchor();
    error InvalidRegistry();
    error InvalidAddress();
    error DuplicateAddress();
    error InvalidDeadlines();
    error UnregisteredMachine();
    error BatchWindowClosed();
    error BatchAlreadyPosted();
    error InvalidPayload();
    error InvalidSignature();
    error WrongSigner();
    error UnregisteredTrustee();
    error PartialWindowClosed();
    error PartialAlreadyPosted();
    error WrongSnapshot();
    error InvalidAggregateDigest();

    event BatchPosted(address indexed machine, bytes payload, bytes signature, bytes32 payloadHash);
    event PartialPosted(uint256 indexed trusteeIndex, bytes32 aggregateDigest, bytes payload, bytes32 payloadHash);

    constructor(
        bytes32 electionId_,
        bytes32 manifestHash_,
        address[] memory machines_,
        address[] memory trustees_,
        uint64 batchDeadline_,
        uint64 partialDeadline_
    ) {
        if (electionId_ == bytes32(0) || manifestHash_ == bytes32(0)) {
            revert InvalidAnchor();
        }
        if (machines_.length == 0 || machines_.length > 16 || trustees_.length != 5) {
            revert InvalidRegistry();
        }
        if (batchDeadline_ <= block.timestamp || partialDeadline_ <= batchDeadline_) revert InvalidDeadlines();

        electionId = electionId_;
        manifestHash = manifestHash_;
        batchDeadline = batchDeadline_;
        partialDeadline = partialDeadline_;

        for (uint256 i; i < machines_.length; ++i) {
            address machine = machines_[i];
            if (machine == address(0)) revert InvalidAddress();
            if (_registeredMachines[machine]) revert DuplicateAddress();
            _registeredMachines[machine] = true;
            _machines.push(machine);
        }
        for (uint256 i; i < trustees_.length; ++i) {
            address trustee = trustees_[i];
            if (trustee == address(0)) revert InvalidAddress();
            if (_registeredTrustees[trustee]) revert DuplicateAddress();
            _registeredTrustees[trustee] = true;
            _trustees.push(trustee);
        }

        snapshotHash = keccak256(abi.encode(block.chainid, address(this), electionId_, manifestHash_));
    }

    function deadlines() external view returns (uint64 batch, uint64 partialUntil) {
        return (batchDeadline, partialDeadline);
    }

    function machineCount() external view returns (uint256) {
        return _machines.length;
    }

    function trusteeCount() external pure returns (uint256) {
        return 5;
    }

    function machineAt(uint256 index) external view returns (address) {
        return _machines[index];
    }

    function trusteeAt(uint256 index) external view returns (address) {
        return _trustees[index];
    }

    function postBatch(bytes calldata payload, bytes calldata signature) external {
        if (!_registeredMachines[msg.sender]) revert UnregisteredMachine();
        if (block.timestamp > batchDeadline) revert BatchWindowClosed();
        if (batchPosted[msg.sender]) revert BatchAlreadyPosted();
        if (payload.length == 0 || payload.length > MAX_PAYLOAD_LENGTH || signature.length != 65) {
            revert InvalidPayload();
        }

        bytes32 batchDigest = keccak256(abi.encode(block.chainid, address(this), electionId, keccak256(payload)));
        bytes32 signedDigest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", batchDigest));
        address signer = _recover(signedDigest, signature);
        if (signer != msg.sender) revert WrongSigner();

        batchPosted[msg.sender] = true;
        bytes32 payloadHash = keccak256(payload);
        snapshotHash = keccak256(abi.encode(snapshotHash, msg.sender, payloadHash));
        emit BatchPosted(msg.sender, payload, signature, payloadHash);
    }

    function postPartial(bytes32 expectedSnapshot, bytes32 aggregateDigest, bytes calldata payload) external {
        if (!_registeredTrustees[msg.sender]) revert UnregisteredTrustee();
        if (block.timestamp <= batchDeadline || block.timestamp > partialDeadline) {
            revert PartialWindowClosed();
        }
        uint256 trusteeIndex = _trusteeIndex(msg.sender);
        if (partialPosted[trusteeIndex]) revert PartialAlreadyPosted();
        if (expectedSnapshot != snapshotHash) revert WrongSnapshot();
        if (aggregateDigest == bytes32(0)) revert InvalidAggregateDigest();
        if (payload.length == 0 || payload.length > MAX_PAYLOAD_LENGTH) revert InvalidPayload();

        if (pinnedAggregateDigest != bytes32(0) && pinnedAggregateDigest != aggregateDigest) {
            revert InvalidAggregateDigest();
        }
        partialPosted[trusteeIndex] = true;
        if (pinnedAggregateDigest == bytes32(0)) {
            uint256 votes = ++_aggregateDigestVotes[aggregateDigest];
            if (votes == 3) pinnedAggregateDigest = aggregateDigest;
        }
        emit PartialPosted(trusteeIndex, aggregateDigest, payload, keccak256(payload));
    }

    function _trusteeIndex(address trustee) private view returns (uint256) {
        for (uint256 i; i < _trustees.length; ++i) {
            if (_trustees[i] == trustee) return i + 1;
        }
        revert UnregisteredTrustee();
    }

    function _recover(bytes32 digest, bytes calldata signature) private pure returns (address) {
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly ("memory-safe") {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        if (v != 27 && v != 28) revert InvalidSignature();
        if (uint256(s) == 0 || uint256(s) > SECP256K1_HALF_ORDER) revert InvalidSignature();
        address signer = ecrecover(digest, v, r, s);
        if (signer == address(0)) revert InvalidSignature();
        return signer;
    }
}
