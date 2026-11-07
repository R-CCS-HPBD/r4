export {
  CompanionBridgeClient,
  getCompanionBridge,
  setCompanionBridgeForTests,
  shutdownCompanionBridge,
  resolveBridgePython,
  bridgeErrorHint,
  BridgeRequestError,
} from "./jsonl-client";
export {
  mapBridgeStateToWorkspace,
  applyBridgeStatePatch,
  type BridgeStatePatch,
} from "./map-state";
export { respondToolApproval } from "./approval-bridge";
export { wireAskUserBridge, respondAskUser, cancelAskUser } from "./ask-user-bridge";
export { wireCompanionRuntimeBridge } from "./companion-runtime-bridge";
