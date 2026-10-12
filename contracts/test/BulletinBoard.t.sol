// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.28;

import "../src/BulletinBoard.sol";

interface Vm {
    struct Log {
        bytes32[] topics;
        bytes data;
        address emitter;
    }

    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest) external returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address sender) external;
    function warp(uint256 timestamp) external;
    function chainId(uint256 newChainId) external;
    function recordLogs() external;
    function getRecordedLogs() external returns (Log[] memory);
    function expectRevert(bytes4 revertSelector) external;
}

contract BulletinBoardTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 private constant MACHINE_KEY = 0xA11CE;
    uint256 private constant OTHER_MACHINE_KEY = 0xB0B;
    uint256 private constant MACHINE_KEY_2 = 0xA11CF;
    uint256 private constant MACHINE_KEY_3 = 0xA11D0;
    uint256 private constant TRUSTEE_KEY_1 = 0x1001;
    uint256 private constant TRUSTEE_KEY_2 = 0x1002;
    uint256 private constant TRUSTEE_KEY_3 = 0x1003;
    uint256 private constant TRUSTEE_KEY_4 = 0x1004;
    uint256 private constant TRUSTEE_KEY_5 = 0x1005;
    uint256 private constant SECP256K1_ORDER = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141;

    bytes32 private constant ELECTION = keccak256("election-1");
    bytes32 private constant MANIFEST = keccak256("manifest-1");
    uint64 private constant BATCH_DEADLINE = 1000;
    uint64 private constant PARTIAL_DEADLINE = 2000;

    BulletinBoard private board;
    address private machine;
    address private rogueMachine;
    address private machine2;
    address private machine3;
    address[] private trustees;

    function setUp() public {
        vm.warp(1);
        machine = vm.addr(MACHINE_KEY);
        rogueMachine = vm.addr(OTHER_MACHINE_KEY);
        machine2 = vm.addr(MACHINE_KEY_2);
        machine3 = vm.addr(MACHINE_KEY_3);
        trustees = new address[](5);
        trustees[0] = vm.addr(TRUSTEE_KEY_1);
        trustees[1] = vm.addr(TRUSTEE_KEY_2);
        trustees[2] = vm.addr(TRUSTEE_KEY_3);
        trustees[3] = vm.addr(TRUSTEE_KEY_4);
        trustees[4] = vm.addr(TRUSTEE_KEY_5);
        address[] memory machines = new address[](3);
        machines[0] = machine;
        machines[1] = machine2;
        machines[2] = machine3;
        board = new BulletinBoard(ELECTION, MANIFEST, machines, trustees, BATCH_DEADLINE, PARTIAL_DEADLINE);
    }

    function testSetupGettersAndInitialSnapshot() public view {
        require(board.electionId() == ELECTION, "election getter");
        require(board.manifestHash() == MANIFEST, "manifest getter");
        (uint64 batch, uint64 partialUntil) = board.deadlines();
        require(batch == BATCH_DEADLINE && partialUntil == PARTIAL_DEADLINE, "deadlines getter");
        require(board.machineCount() == 3, "machine count");
        require(board.machineAt(0) == machine && board.machineAt(1) == machine2, "machine order");
        require(board.machineAt(2) == machine3, "machine order last");
        require(board.trusteeCount() == 5, "trustee count");
        for (uint256 i; i < 5; ++i) {
            require(board.trusteeAt(i) == trustees[i], "trustee order");
        }
        require(
            board.snapshotHash() == keccak256(abi.encode(block.chainid, address(board), ELECTION, MANIFEST)),
            "initial snapshot"
        );
    }

    function testConstructorRejectsInvalidSetup() public {
        address[] memory machines = new address[](1);
        machines[0] = machine;
        address[] memory fiveTrustees = _trustees();
        vm.expectRevert(BulletinBoard.InvalidAnchor.selector);
        new BulletinBoard(bytes32(0), MANIFEST, machines, fiveTrustees, 1000, 2000);
        vm.expectRevert(BulletinBoard.InvalidAnchor.selector);
        new BulletinBoard(ELECTION, bytes32(0), machines, fiveTrustees, 1000, 2000);
        vm.expectRevert(BulletinBoard.InvalidRegistry.selector);
        new BulletinBoard(ELECTION, MANIFEST, new address[](0), fiveTrustees, 1000, 2000);
        vm.expectRevert(BulletinBoard.InvalidRegistry.selector);
        new BulletinBoard(ELECTION, MANIFEST, machines, new address[](4), 1000, 2000);
        vm.expectRevert(BulletinBoard.InvalidDeadlines.selector);
        new BulletinBoard(ELECTION, MANIFEST, machines, fiveTrustees, 1, 2000);
        vm.expectRevert(BulletinBoard.InvalidDeadlines.selector);
        new BulletinBoard(ELECTION, MANIFEST, machines, fiveTrustees, 1000, 1000);

        machines[0] = address(0);
        vm.expectRevert(BulletinBoard.InvalidAddress.selector);
        new BulletinBoard(ELECTION, MANIFEST, machines, fiveTrustees, 1000, 2000);
        machines[0] = machine;
        address[] memory duplicateMachines = new address[](2);
        duplicateMachines[0] = machine;
        duplicateMachines[1] = machine;
        vm.expectRevert(BulletinBoard.DuplicateAddress.selector);
        new BulletinBoard(ELECTION, MANIFEST, duplicateMachines, fiveTrustees, 1000, 2000);
        fiveTrustees[4] = fiveTrustees[0];
        vm.expectRevert(BulletinBoard.DuplicateAddress.selector);
        new BulletinBoard(ELECTION, MANIFEST, machines, fiveTrustees, 1000, 2000);
        fiveTrustees[4] = address(0);
        vm.expectRevert(BulletinBoard.InvalidAddress.selector);
        new BulletinBoard(ELECTION, MANIFEST, machines, fiveTrustees, 1000, 2000);
    }

    function testBatchPostsFullEventAndCorrectSnapshotFold() public {
        bytes memory payload = hex"7b2263617374223a5b5d7d";
        bytes memory signature = _signBatch(MACHINE_KEY, payload, block.chainid, address(board), ELECTION);
        bytes32 payloadHash = keccak256(payload);
        bytes32 expectedSnapshot = keccak256(abi.encode(board.snapshotHash(), machine, payloadHash));
        vm.recordLogs();
        vm.prank(machine);
        board.postBatch(payload, signature);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].emitter == address(board), "batch log count");
        require(logs[0].topics[0] == keccak256("BatchPosted(address,bytes,bytes,bytes32)"), "batch topic");
        require(address(uint160(uint256(logs[0].topics[1]))) == machine, "machine topic");
        (bytes memory loggedPayload, bytes memory loggedSignature, bytes32 loggedHash) =
            abi.decode(logs[0].data, (bytes, bytes, bytes32));
        require(keccak256(loggedPayload) == keccak256(payload), "full payload");
        require(keccak256(loggedSignature) == keccak256(signature), "full signature");
        require(loggedHash == payloadHash, "payload hash");
        require(board.snapshotHash() == expectedSnapshot, "fold digest");
        require(board.batchPosted(machine), "posted flag");
    }

    function testThreeRegisteredMachinesAppendIndependentBatches() public {
        address[3] memory senders = [machine3, machine, machine2];
        uint256[3] memory keys = [MACHINE_KEY_3, MACHINE_KEY, MACHINE_KEY_2];
        bytes32 expected = board.snapshotHash();
        for (uint256 i; i < 3; ++i) {
            bytes memory payload = abi.encodePacked(uint8(i + 1));
            vm.prank(senders[i]);
            board.postBatch(payload, _signBatch(keys[i], payload, block.chainid, address(board), ELECTION));
            expected = keccak256(abi.encode(expected, senders[i], keccak256(payload)));
            require(board.batchPosted(senders[i]), "registered machine posted");
        }
        require(board.snapshotHash() == expected, "ordered batch fold");
    }

    function testBatchRejectsRogueDuplicateWrongSignerAndEditedPayload() public {
        bytes memory payload = hex"010203";
        bytes memory signature = _signBatch(MACHINE_KEY, payload, block.chainid, address(board), ELECTION);
        vm.prank(rogueMachine);
        vm.expectRevert(BulletinBoard.UnregisteredMachine.selector);
        board.postBatch(payload, signature);
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.WrongSigner.selector);
        board.postBatch(payload, _signBatch(OTHER_MACHINE_KEY, payload, block.chainid, address(board), ELECTION));
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.WrongSigner.selector);
        board.postBatch(hex"010204", signature);
        vm.prank(machine);
        board.postBatch(payload, signature);
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.BatchAlreadyPosted.selector);
        board.postBatch(payload, signature);
    }

    function testBatchSignatureBindsDomainAndRejectsMalformedHighS() public {
        bytes memory payload = hex"010203";
        bytes memory wrongChain = _signBatch(MACHINE_KEY, payload, block.chainid + 1, address(board), ELECTION);
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.WrongSigner.selector);
        board.postBatch(payload, wrongChain);
        bytes memory wrongBoard = _signBatch(MACHINE_KEY, payload, block.chainid, address(0x1234), ELECTION);
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.WrongSigner.selector);
        board.postBatch(payload, wrongBoard);
        bytes memory wrongElection = _signBatch(MACHINE_KEY, payload, block.chainid, address(board), keccak256("other"));
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.WrongSigner.selector);
        board.postBatch(payload, wrongElection);
        bytes memory highS = _signBatch(MACHINE_KEY, payload, block.chainid, address(board), ELECTION);
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly ("memory-safe") {
            r := mload(add(highS, 32))
            s := mload(add(highS, 64))
            v := byte(0, mload(add(highS, 96)))
        }
        bytes32 malleableS = bytes32(SECP256K1_ORDER - uint256(s));
        bytes memory malleable = abi.encodePacked(r, malleableS, v == 27 ? uint8(28) : uint8(27));
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.InvalidSignature.selector);
        board.postBatch(payload, malleable);
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.InvalidPayload.selector);
        board.postBatch(payload, hex"0102");
        bytes memory invalidV = _signBatch(MACHINE_KEY, payload, block.chainid, address(board), ELECTION);
        invalidV[64] = bytes1(uint8(0));
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.InvalidSignature.selector);
        board.postBatch(payload, invalidV);
    }

    function testBatchDeadlineAndPayloadShape() public {
        bytes memory empty;
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.InvalidPayload.selector);
        board.postBatch(empty, _signBatch(MACHINE_KEY, hex"", block.chainid, address(board), ELECTION));
        vm.warp(BATCH_DEADLINE + 1);
        bytes memory payload = hex"01";
        vm.prank(machine);
        vm.expectRevert(BulletinBoard.BatchWindowClosed.selector);
        board.postBatch(payload, _signBatch(MACHINE_KEY, payload, block.chainid, address(board), ELECTION));
    }

    function testPartialWindowSnapshotPinAndIndexOrder() public {
        bytes32 aggregate = keccak256("aggregate");
        bytes memory payload = hex"aabbcc";
        bytes32 snapshot = board.snapshotHash();
        vm.prank(trustees[2]);
        vm.expectRevert(BulletinBoard.PartialWindowClosed.selector);
        board.postPartial(snapshot, aggregate, payload);
        vm.warp(BATCH_DEADLINE + 1);
        vm.recordLogs();
        vm.prank(trustees[2]);
        board.postPartial(snapshot, aggregate, payload);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        require(logs.length == 1 && logs[0].emitter == address(board), "partial log count");
        require(logs[0].topics[0] == keccak256("PartialPosted(uint256,bytes32,bytes,bytes32)"), "partial topic");
        require(uint256(logs[0].topics[1]) == 3, "manifest ordinal");
        (bytes32 loggedAggregate, bytes memory loggedPayload, bytes32 loggedHash) =
            abi.decode(logs[0].data, (bytes32, bytes, bytes32));
        require(loggedAggregate == aggregate && keccak256(loggedPayload) == keccak256(payload), "partial data");
        require(loggedHash == keccak256(payload), "partial hash");
        require(board.pinnedAggregateDigest() == aggregate, "pin");
        require(board.partialPosted(3), "partial flag");

        vm.prank(trustees[0]);
        board.postPartial(snapshot, aggregate, hex"dd");
        vm.prank(trustees[4]);
        board.postPartial(snapshot, aggregate, hex"ee");
        require(board.partialPosted(1) && board.partialPosted(5), "multi trustee postings");
    }

    function testPartialRejectsRogueDuplicateWrongSnapshotAndPinMismatch() public {
        vm.warp(BATCH_DEADLINE + 1);
        bytes32 snapshot = board.snapshotHash();
        bytes32 aggregate = keccak256("aggregate");
        vm.prank(rogueMachine);
        vm.expectRevert(BulletinBoard.UnregisteredTrustee.selector);
        board.postPartial(snapshot, aggregate, hex"01");
        vm.prank(trustees[0]);
        vm.expectRevert(BulletinBoard.WrongSnapshot.selector);
        board.postPartial(bytes32(uint256(1)), aggregate, hex"01");
        vm.prank(trustees[0]);
        vm.expectRevert(BulletinBoard.InvalidAggregateDigest.selector);
        board.postPartial(snapshot, bytes32(0), hex"01");
        vm.prank(trustees[0]);
        vm.expectRevert(BulletinBoard.InvalidPayload.selector);
        board.postPartial(snapshot, aggregate, hex"");
        vm.prank(trustees[0]);
        board.postPartial(snapshot, aggregate, hex"01");
        vm.prank(trustees[0]);
        vm.expectRevert(BulletinBoard.PartialAlreadyPosted.selector);
        board.postPartial(snapshot, aggregate, hex"01");
        vm.prank(trustees[1]);
        vm.expectRevert(BulletinBoard.InvalidAggregateDigest.selector);
        board.postPartial(snapshot, keccak256("different"), hex"01");
        require(!board.partialPosted(2), "failed pin did not consume trustee");
    }

    function testPartialDeadlineUpperBound() public {
        vm.warp(PARTIAL_DEADLINE + 1);
        bytes32 snapshot = board.snapshotHash();
        vm.prank(trustees[0]);
        vm.expectRevert(BulletinBoard.PartialWindowClosed.selector);
        board.postPartial(snapshot, keccak256("aggregate"), hex"01");
    }

    function _signBatch(
        uint256 privateKey,
        bytes memory payload,
        uint256 chainId_,
        address boardAddress,
        bytes32 electionId_
    ) private returns (bytes memory) {
        bytes32 payloadHash = keccak256(payload);
        bytes32 batchDigest = keccak256(abi.encode(chainId_, boardAddress, electionId_, payloadHash));
        bytes32 signedDigest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", batchDigest));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, signedDigest);
        return abi.encodePacked(r, s, v);
    }

    function _trustees() private view returns (address[] memory list) {
        list = new address[](5);
        for (uint256 i; i < 5; ++i) {
            list[i] = trustees[i];
        }
    }
}
