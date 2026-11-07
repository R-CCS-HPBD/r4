/**
 * R4 Console v3 data adapters.
 *
 * Platform adapter layer abstracts backend differences.
 */

export type * from "./types";
export { OfflineDataSource } from "./offline";
export { R4CliDataSource } from "./r4-cli";
export { CompanionChatBackend, createCompanionChatBackend } from "./companion-chat";
export {
  createPlatformAdapter,
  inferPlatformKind,
  R4PlatformAdapter,
} from "./platform";
export * from "./mappers";
