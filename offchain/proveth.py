import argparse
import json
import sys
from typing import List, Tuple

from ethereum import (
    block,
    bloom,
    messages,
    transactions,
    utils
)
import pprint
import requests
import rlp
from rlp import sedes
from trie import HexaryTrie
from trie.constants import (
    BLANK_NODE,
    BLANK_NODE_HASH,
    NODE_TYPE_BLANK,
    NODE_TYPE_LEAF,
    NODE_TYPE_EXTENSION,
    NODE_TYPE_BRANCH,
    BLANK_HASH,
)
from trie.utils.nodes import *
from trie.utils.nibbles import encode_nibbles, decode_nibbles, bytes_to_nibbles

MODULE_DEBUG = False

def rec_hex(x):
    if isinstance(x, list):
        return [rec_hex(elem) for elem in x]
    else:
        return utils.encode_hex(x)

def rec_bin(x):
    if isinstance(x, list):
        return [rec_bin(elem) for elem in x]
    elif isinstance(x, int):
        return x
    elif isinstance(x, str):
        if x.startswith("0x"):
            if len(x) != 2:
                return utils.decode_hex(x[2:])
            else:
                return 0
        else:
            return utils.decode_hex(x)
    elif x is None:
        return 0

def normalize_bytes(hash):
    if isinstance(hash, str):
        if hash.startswith("0x"):
            hash = hash[2:]
        if len(hash) % 2 != 0:
            hash = '0' + hash
        return utils.decode_hex(hash)
    else:
        return bytes(hash)

def str2bool(v):
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

def get_args():
    parser = argparse.ArgumentParser(
        description="Patricia Merkle Trie Proof Generating Tool",
        formatter_class=argparse.RawTextHelpFormatter)
    # TODO add stuff around adding a block header and then generating proofs of
    # inclusion / exclusion etc etc etc
    blockInfoGroup = parser.add_mutually_exclusive_group(required=True)
    blockInfoGroup.add_argument('-n', '--block-number',
                        default="",
                        help="Block number that transaction exists in")
    blockInfoGroup.add_argument('-b', '--block-hash',
                        default="",
                        help="Block hash that transaction exists in")
    parser.add_argument('-tr', '--transaction_receipt_mode', required=False,
                        type=str2bool, default="False",
                        help="If the proof should be for a transaction receipt")
    parser.add_argument('-i', '--transaction_index', required=True, type=int,
                        default="",
                        help="Zero-based index of the transaction in the "
                        "block (e.g. the third transaction in the block is at "
                        "index 2)")
    parser.add_argument('-r', '--rpc', required=True,
                        default="",
                        help="URL of web3 rpc node. (e.g. "
                        "http://localhost:8545)")
    parser.add_argument('-v', '--verbose', required=False, action='store_true',
                        help="Print verbose output")
    return parser.parse_args()

def block_header(block_dict: dict):
    b = block.BlockHeader(
        normalize_bytes(block_dict["parentHash"]),
        normalize_bytes(block_dict["sha3Uncles"]),
        utils.normalize_address(block_dict["miner"]),
        normalize_bytes(block_dict["stateRoot"]),
        normalize_bytes(block_dict["transactionsRoot"]),
        normalize_bytes(block_dict["receiptsRoot"]),
        utils.bytes_to_int(normalize_bytes(block_dict["logsBloom"])),
        utils.parse_as_int(block_dict['difficulty']),
        utils.parse_as_int(block_dict['number']),
        utils.parse_as_int(block_dict['gasLimit']),
        utils.parse_as_int(block_dict['gasUsed']),
        utils.parse_as_int(block_dict['timestamp']),
        normalize_bytes(block_dict["extraData"]),
        normalize_bytes(block_dict["mixHash"]),
        normalize_bytes(block_dict["nonce"]),
    )
    if normalize_bytes(block_dict["hash"]) != b.hash:
        raise ValueError("Blockhash does not match. Received invalid block "
                         "header?")
    return b

def rlp_transaction(tx_dict: dict):
    t = transactions.Transaction(
        utils.parse_as_int(tx_dict['nonce']),
        utils.parse_as_int(tx_dict['gasPrice']),
        utils.parse_as_int(tx_dict['gas']),
        normalize_bytes(tx_dict['to'] or ''),
        utils.parse_as_int(tx_dict['value']),
        utils.decode_hex(tx_dict['input']),
        utils.parse_as_int(tx_dict['v']),
        utils.bytes_to_int(normalize_bytes(tx_dict['r'])),
        utils.bytes_to_int(normalize_bytes(tx_dict['s'])),
    )
    if normalize_bytes(tx_dict['hash']) != t.hash:
        raise ValueError("Tx hash does not match. Received invalid transaction?")
    return rlp.encode(t)


def generate_proof(mpt, mpt_key_nibbles: bytes):
    if not all(nibble < 16 for nibble in mpt_key_nibbles):
        raise ValueError("mpt_key_nibbles has non-nibble elements")
    EMPTY = 128
    stack_indexes = []
    mpt_path = []
    stack = []

    def aux(node_hash, mpt_key_nibbles):
        nonlocal stack_indexes
        nonlocal mpt_path
        nonlocal stack

        node = mpt.get_node(node_hash)
        if get_node_type(node) == NODE_TYPE_BLANK:
            if MODULE_DEBUG:
                print("Hit an empty node, returning")
            return
        elif get_node_type(node) == NODE_TYPE_BRANCH:
            if MODULE_DEBUG:
                print("Hit a branch node")
            if mpt_key_nibbles:
                i = mpt_key_nibbles[0]
                stack_indexes.append(i)
                stack.append(node)
                mpt_path.append(i)
                aux(node[i], mpt_key_nibbles[1:])
            else:
                i = 16
                stack_indexes.append(i)
                stack.append(node)
        elif get_node_type(node) in [NODE_TYPE_EXTENSION, NODE_TYPE_LEAF]:
            if MODULE_DEBUG:
                print("Hit an extension/branch node")
            key = extract_key(node)
            prefix, key_remainder, mpt_key_nibbles_remainder = \
                    consume_common_prefix(key, mpt_key_nibbles)
            if not key_remainder:
                if MODULE_DEBUG:
                    print("Non-divergent leaf/extension")
                stack_indexes.append(1)
                stack.append(node)
                mpt_path += prefix
                if get_node_type(node) == NODE_TYPE_EXTENSION:
                    aux(node[1], mpt_key_nibbles_remainder)
            else:
                if MODULE_DEBUG:
                    print("Divergent leaf/extension")
                stack_indexes.append(0xff)
                stack.append(node)
                mpt_path += prefix
        else:
            raise ValueError("Unknown node type: {}".format(
                get_node_type(node)))


    root_node = mpt.get_node(mpt.root_hash)
    if get_node_type(root_node) == NODE_TYPE_BLANK:
        if MODULE_DEBUG:
            print("Blank root node")
    else:
        aux(mpt.root_hash, mpt_key_nibbles)

    if MODULE_DEBUG:
        print('key nibbles: ', mpt_key_nibbles)
        print('Stack:       ', rec_hex(stack))
        print('StackIndexes:', stack_indexes)
        print('mpt_path:    ', mpt_path)

    return (mpt_path, stack_indexes, stack)

def generate_proof_blob(block_dict, tx_index):
    header = block_header(block_dict)

    mpt = HexaryTrie(db={})
    for tx_dict in block_dict["transactions"]:
        key = rlp.encode(utils.parse_as_int(tx_dict['transactionIndex']))
        mpt.set(key, rlp_transaction(tx_dict))

    if mpt.root_hash != normalize_bytes(block_dict['transactionsRoot']):
        raise ValueError("Tx trie root hash does not match.")

    mpt_key_nibbles = bytes_to_nibbles(rlp.encode(tx_index))
    mpt_path, stack_indexes, stack = generate_proof(mpt, mpt_key_nibbles)

    proof_blob = rlp.encode([
        1, # proof_type
        header,
        tx_index,
        bytes(mpt_path),
        bytes(stack_indexes),
        stack,
    ])
    return proof_blob

def decode_int_from_hex(x):
    return utils.decode_int(utils.decode_hex(x).lstrip(b"\x00"))

def get_receipt_from_jsonrpc(response, previousGasUsed):
    if MODULE_DEBUG:
        print(response)
    assert response['jsonrpc'] == '2.0'
    assert 'id' in response
    assert 'result' in response
    receipt = response['result']
    logs = []
    for log in receipt['logs']:
        topics = [decode_int_from_hex(x) for x in log['topics']]
        logs.append(messages.Log(
            address = utils.normalize_address(log['address']),
            topics = topics,
            data = utils.decode_hex(log['data'])))
    # pre Byzantium returns a root
    if 'root' in receipt:
        return messages.Receipt(
            state_root = normalize_bytes(receipt['root']),
            gas_used = utils.parse_as_int(receipt['gasUsed']),
            bloom = utils.parse_as_int(receipt['logsBloom']),
            logs = logs)
    receipt = messages.Receipt(
        state_root = (b'\x01' if receipt['status'] else b''),
        gas_used = utils.parse_as_int(receipt['cumulativeGasUsed']),
        bloom = utils.parse_as_int(receipt['logsBloom']),
        logs = logs)
    if MODULE_DEBUG:
        print("Rlp encoded receipt:")
        print(rec_hex(rlp.encode(receipt)))
    return receipt


def get_receipt(url, transaction_hash, previousGasUsed):
    request = {
        "jsonrpc":"2.0",
        "method":"eth_getTransactionReceipt",
        "params":[transaction_hash],
        "id":1,
    }
    if MODULE_DEBUG:
        print(request)
    r = requests.post(url, json=request)
    r.raise_for_status()
    return get_receipt_from_jsonrpc(r.json(), previousGasUsed)

def generate_proof_blob_receipt(block_dict, tx_index, url):
    header = block_header(block_dict)

    mpt = HexaryTrie(db={})
    gas_used = 0
    for tx_dict in block_dict["transactions"]:
        key = rlp.encode(utils.parse_as_int(tx_dict['transactionIndex']))
        receipt = get_receipt(url, tx_dict['hash'], gas_used)
        gas_used = receipt.as_dict()['gas_used']
        mpt.set(key, rlp.encode(receipt))

    if mpt.root_hash != normalize_bytes(block_dict['receiptsRoot']):
        if MODULE_DEBUG:
            print("mpt.root_hash " + str(utils.encode_hex(mpt.root_hash)))
            print("receiptRoot " +
                  str(normalize_bytes(utils.encode_hex(block_dict['receiptsRoot']))))
        raise ValueError("Block receiptRoot hash does not match.")

    return ""

def generate_proof_blob_from_jsonrpc_response(response, tx_index):
    if MODULE_DEBUG:
        print(response)
    assert response['jsonrpc'] == '2.0'
    assert 'id' in response
    assert 'result' in response
    return generate_proof_blob(response['result'], tx_index)

def generate_proof_blob_receipt_from_jsonrpc_response(response, tx_index, url):
    if MODULE_DEBUG:
        print(response)
    assert response['jsonrpc'] == '2.0'
    assert 'id' in response
    assert 'result' in response
    return generate_proof_blob_receipt(response['result'], tx_index, url)


def generate_proof_blob_from_jsonrpc_using_hash(url, block_hash, tx_index):
    request = {
        "jsonrpc":"2.0",
        "method":"eth_getBlockByHash",
        "params":['0x' + utils.encode_hex(block_hash), True],
        "id":1,
    }
    if MODULE_DEBUG:
        print(request)
    r = requests.post(url, json=request)
    r.raise_for_status()
    return generate_proof_blob_from_jsonrpc_response(r.json(), tx_index)

def generate_proof_blob_from_jsonrpc_using_number(url, block_number, tx_index):
    request = {
        "jsonrpc":"2.0",
        "method":"eth_getBlockByNumber",
        "params":[str(hex(block_number)), True],
        "id":1,
    }
    if MODULE_DEBUG:
        print(request)
    r = requests.post(url, json=request)
    r.raise_for_status()
    return generate_proof_blob_from_jsonrpc_response(r.json(), tx_index)

def generate_proof_blob_receipt_from_jsonrpc_using_hash(url,
                                                        block_hash,
                                                        tx_index):
    request = {
        "jsonrpc":"2.0",
        "method":"eth_getBlockByHash",
        "params":['0x' + utils.encode_hex(block_hash), True],
        "id":1,
    }
    if MODULE_DEBUG:
        print(request)
    r = requests.post(url, json=request)
    r.raise_for_status()
    return generate_proof_blob_receipt_from_jsonrpc_response(
        r.json(), tx_index, url)

def main():
    args = get_args()
    if args.verbose:
        global MODULE_DEBUG
        MODULE_DEBUG = True
    if args.transaction_receipt_mode:
        proof_blob = generate_proof_blob_receipt_from_jsonrpc_using_hash(
            args.rpc, utils.decode_hex(args.block_hash), args.transaction_index)
    elif args.block_hash:
        proof_blob = generate_proof_blob_from_jsonrpc_using_hash(
            args.rpc, utils.decode_hex(args.block_hash), args.transaction_index)
    elif args.block_number:
        proof_blob = generate_proof_blob_from_jsonrpc_using_number(
            args.rpc, int(args.block_number), args.transaction_index)
    else:
        print("Either --block-hash or --block-number are required")
        exit(1)

    print("Final Output: ")
    #print(rec_hex(proof_blob))
    exit(0)

if __name__ == "__main__":
    main()
