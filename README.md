# Provth

![Python >= 3.6](https://img.shields.io/badge/python-%3E%3D%203.6-blue.svg)
![Solidity >= 0.5.0](https://img.shields.io/badge/solidity-%3E%3D%200.5.0-blue.svg)

Ethereum's design [makes heavy use](https://blog.ethereum.org/2015/11/15/merkling-in-ethereum/) of [Merkle trees](https://en.wikipedia.org/wiki/Merkle_tree) enabling *light clients* to interact with the blockchain without having to download full blocks or its complete state.

Ethereum uses its own variant of Merkle trees, called [Merkle Patricia Tries](https://github.com/ethereum/wiki/wiki/Patricia-Tree), which provide a [dictionary](https://en.wikipedia.org/wiki/Associative_array)-like interface and enable the generation and verification of small proofs (logarithmic in the number of items in the dictionary) that a given key-value-pair is present/absent from the dictionary. Ethereum uses Merkle Patricia Tries to store transactions, transactions receipts, and the *state* (all accounts with their balances, code, and storage).
