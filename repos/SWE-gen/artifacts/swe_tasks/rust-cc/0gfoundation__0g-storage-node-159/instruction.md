Peers on an incompatible network can currently connect and successfully complete the STATUS RPC exchange, which allows nodes from a different network to remain connected and continue interacting. The STATUS handshake must validate that the remote peer’s network identifier matches the local node’s configured network id.

When a node receives a STATUS message (either as an incoming request or as a response to an outgoing request), it should compare the message’s network id to its own. If the ids do not match, the peer must be treated as incompatible: the node should refuse to proceed with normal protocol interaction and should ban (or otherwise permanently block) the peer so it cannot continue communicating.

Expected behavior:
- STATUS RPC should only succeed between peers on the same network id.
- If a peer sends a STATUS message with a different network id, the node should not accept the peer as compatible.
- The incompatible peer should be banned via the existing reporting/penalization mechanism (e.g., the same mechanism used when calling into reporting with a severity that results in banning), and the connection should be terminated.

Actual behavior:
- STATUS RPC completes successfully even if the remote peer is on a different network id, so the node does not prevent cross-network peers from staying connected.

The implementation should ensure that the STATUS handling path enforces the network id check consistently (both for requests and responses) and that the banning action is triggered in the incompatible case so the peer is blocked from future interactions.