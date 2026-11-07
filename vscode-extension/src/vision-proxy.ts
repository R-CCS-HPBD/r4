/**
 * Vision proxy for text-only R4 LM models.
 *
 * When the selected model lacks imageInput, image attachments are described by
 * a ready R4 vision/VLM endpoint, then injected as text. The endpoint is the
 * platform's, even when the chat model is on a user-registered public source
 * (ADR-106 D-10, 2026-11-03: service models are used implicitly).
 */

import * as vscode from "vscode";
import type {
  GatewayClient,
  Model,
  OpenAIChatMessage,
  OpenAIMessageContentPart,
} from "./gateway-client";

/** A configured source, as the provider lists them (auth.ts LlmSourceConfig). */
export interface VisionSourceConfig {
  name: string;
  url: string;
  token: string;
  kind: "platform" | "public";
}

/**
 * The client whose vision models describe images. A public chat source lists
 * no platform vision models, so the platform source's client is used instead;
 * a platform chat source keeps its own client (same Gateway). Without any
 * platform source the chat client is kept (the old behaviour).
 */
export function pickVisionClient(
  chatClient: GatewayClient,
  sources: readonly VisionSourceConfig[],
  makeClient: (source: VisionSourceConfig) => GatewayClient,
): GatewayClient {
  if (!chatClient.isPublic()) {
    return chatClient;
  }
  const platform = sources.find((source) => source.kind === "platform");
  return platform ? makeClient(platform) : chatClient;
}

const IMAGE_DESCRIPTION_PREFIX = "[Image description]\n";
const IMAGE_DESCRIPTION_SUFFIX = "\n[/Image description]";
const IMAGE_OMITTED_NOTE = "[Earlier image attachment omitted]";
const IMAGE_UNAVAILABLE =
  "[Image description unavailable - provision a vision endpoint or set r4.visionProxyModel]";

const DEFAULT_VISION_PROMPT =
  "Describe all image attachments in this message.\n\n" +
  "If there is one image, describe it directly.\n" +
  "If there are multiple images:\n" +
  "1. Describe each image separately, preserving their order.\n" +
  "2. Then provide a combined description explaining the overall context.\n\n" +
  "Return one concise factual description suitable for a text-only chat prompt. " +
  "Include visible text, objects, UI elements, and relevant context. Do not invent details.";

export interface VisionProxyResolution {
  messages: readonly vscode.LanguageModelChatRequestMessage[];
  /** Optional notice to show once before the model response. */
  notice?: string;
}

/** Catalog models that can serve as a vision proxy (multimodal LLMs and VLMs). */
export function isVisionCatalogModel(model: Model): boolean {
  if (model.capabilities?.supports_vision === true) {
    return true;
  }
  const type = (model.model_type || "").toLowerCase();
  return type === "vision" || type === "vlm";
}

/** Lowercase catalog aliases for a model (name, id, huggingface_id, HF basename). */
export function collectCatalogModelKeys(model: Model): string[] {
  const keys = new Set<string>();
  for (const raw of [model.name, model.id, model.model_id, model.huggingface_id]) {
    if (!raw) {
      continue;
    }
    const lower = raw.toLowerCase();
    keys.add(lower);
    const slash = lower.lastIndexOf("/");
    if (slash >= 0 && slash < lower.length - 1) {
      keys.add(lower.slice(slash + 1));
    }
  }
  return [...keys];
}

/** True when ``llmId`` is the catalog model or a deployment variant (``catalogKey-variant``). */
export function catalogKeyMatchesLlmId(catalogKey: string, llmId: string): boolean {
  const key = catalogKey.toLowerCase();
  const id = llmId.toLowerCase();
  return id === key || id.startsWith(`${key}-`);
}

/** Catalog keys for models that support native vision or can proxy images. */
export function collectVisionCatalogKeys(catalog: Model[]): string[] {
  const keys = new Set<string>();
  for (const model of catalog) {
    if (!isVisionCatalogModel(model)) {
      continue;
    }
    for (const key of collectCatalogModelKeys(model)) {
      keys.add(key);
    }
  }
  return [...keys];
}

export function llmIdMatchesVisionCatalog(llmId: string, visionCatalogKeys: string[]): boolean {
  const id = (llmId || "").toLowerCase();
  if (!id) {
    return false;
  }
  return visionCatalogKeys.some((key) => catalogKeyMatchesLlmId(key, id));
}

/** Ready vision model ids: warm OpenAI models intersected with vision catalog entries. */
export function filterReadyVisionModelIds(
  catalog: Model[],
  llmModelIds: string[],
  excludeModelId?: string,
): string[] {
  const exclude = excludeModelId?.toLowerCase();
  const visionKeys = collectVisionCatalogKeys(catalog);
  const seen = new Set<string>();
  const out: string[] = [];

  for (const id of llmModelIds) {
    const key = id.toLowerCase();
    if (!key || seen.has(key)) {
      continue;
    }
    if (exclude && key === exclude) {
      continue;
    }
    if (!llmIdMatchesVisionCatalog(key, visionKeys)) {
      continue;
    }
    seen.add(key);
    out.push(id);
  }
  return out;
}

export function getVisionProxyPrompt(): string {
  const configured = vscode.workspace
    .getConfiguration("r4")
    .get<string>("visionProxyPrompt", "");
  return configured.trim() || DEFAULT_VISION_PROMPT;
}

export function getConfiguredVisionProxyModel(): string {
  return vscode.workspace.getConfiguration("r4").get<string>("visionProxyModel", "").trim();
}

export async function listReadyVisionModelIds(
  client: GatewayClient,
  excludeModelId?: string,
): Promise<string[]> {
  const [catalog, llmModels] = await Promise.all([
    client.listModels().catch(() => [] as Model[]),
    client.listLLMModels().catch(() => []),
  ]);
  return filterReadyVisionModelIds(
    catalog,
    llmModels.map((m) => m.id),
    excludeModelId,
  );
}

export async function pickVisionProxyModel(
  client: GatewayClient,
  excludeModelId?: string,
): Promise<string | undefined> {
  const ready = await listReadyVisionModelIds(client, excludeModelId);
  if (ready.length === 0) {
    return undefined;
  }

  const configured = getConfiguredVisionProxyModel();
  if (configured) {
    const match = ready.find((id) => id.toLowerCase() === configured.toLowerCase());
    if (match) {
      return match;
    }
  }

  return ready[0];
}

export function messagesHaveImages(
  messages: readonly vscode.LanguageModelChatRequestMessage[],
): boolean {
  return messages.some((message) => getImageParts(message).length > 0);
}

/** Replace image parts with text descriptions when the primary model is text-only. */
export async function resolveVisionProxyMessages(
  client: GatewayClient,
  messages: readonly vscode.LanguageModelChatRequestMessage[],
  primaryModelId: string,
  token: vscode.CancellationToken,
): Promise<VisionProxyResolution> {
  if (!messagesHaveImages(messages)) {
    return { messages };
  }

  const currentImageIndex = findCurrentImageMessageIndex(messages);
  const visionModelId = await pickVisionProxyModel(client, primaryModelId);
  let notice: string | undefined;

  if (!visionModelId && currentImageIndex !== undefined) {
    notice =
      "R4: No ready vision endpoint - image(s) could not be described. " +
      "Provision a vision/VLM model or set r4.visionProxyModel.";
  } else if (visionModelId && currentImageIndex !== undefined) {
    // ADR-106 D-10: the user sees which model described the image.
    notice = `R4: image described by ${visionModelId}.`;
  }

  const result: vscode.LanguageModelChatRequestMessage[] = [];
  for (const [index, message] of messages.entries()) {
    const imageParts = getImageParts(message);
    if (imageParts.length === 0) {
      result.push(message);
      continue;
    }

    const nonImageParts = getNonImageParts(message);
    if (index === currentImageIndex && visionModelId) {
      const description = await describeImages(
        client,
        visionModelId,
        getVisionProxyPrompt(),
        imageParts,
        collectTextFromParts(nonImageParts),
        token,
      );
      const visionText = description ? formatImageDescription(description) : IMAGE_UNAVAILABLE;
      if (!description) {
        notice = "R4: Vision endpoint returned no image description.";
      }
      result.push(createResolvedMessage(message, [
        ...nonImageParts,
        new vscode.LanguageModelTextPart(appendVisionText(nonImageParts, visionText)),
      ]));
      continue;
    }

    if (index === currentImageIndex && !visionModelId) {
      result.push(createResolvedMessage(message, [
        ...nonImageParts,
        new vscode.LanguageModelTextPart(appendVisionText(nonImageParts, IMAGE_UNAVAILABLE)),
      ]));
      continue;
    }

    result.push(createResolvedMessage(message, [
      ...nonImageParts,
      new vscode.LanguageModelTextPart(IMAGE_OMITTED_NOTE),
    ]));
  }

  return { messages: result, notice };
}

export async function configureVisionProxyModel(
  client: GatewayClient | undefined,
): Promise<void> {
  if (!client) {
    void vscode.window.showWarningMessage("R4: Not connected. Login first.");
    return;
  }

  const ready = await listReadyVisionModelIds(client);
  if (ready.length === 0) {
    void vscode.window.showWarningMessage(
      "R4: No ready vision endpoints. Provision a vision/VLM model first.",
    );
    return;
  }

  const current = getConfiguredVisionProxyModel();
  const items = [
    {
      label: "Auto (first ready vision endpoint)",
      description: "Clear r4.visionProxyModel",
      modelId: "",
    },
    ...ready.map((id) => ({
      label: id,
      description: id === current ? "(current)" : "",
      modelId: id,
    })),
  ];

  const pick = await vscode.window.showQuickPick(items, {
    title: "R4: Vision proxy model",
    placeHolder: "Used to describe images for text-only chat models",
  });
  if (!pick) {
    return;
  }

  await vscode.workspace
    .getConfiguration("r4")
    .update("visionProxyModel", pick.modelId, vscode.ConfigurationTarget.Global);

  void vscode.window.showInformationMessage(
    pick.modelId
      ? `R4: Vision proxy set to ${pick.modelId}.`
      : "R4: Vision proxy will auto-select a ready vision endpoint.",
  );
}

function findCurrentImageMessageIndex(
  messages: readonly vscode.LanguageModelChatRequestMessage[],
): number | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === vscode.LanguageModelChatMessageRole.Assistant) {
      return undefined;
    }
    if (
      message.role === vscode.LanguageModelChatMessageRole.User &&
      getImageParts(message).length > 0
    ) {
      return index;
    }
  }
  return undefined;
}

async function describeImages(
  client: GatewayClient,
  visionModelId: string,
  prompt: string,
  images: vscode.LanguageModelDataPart[],
  accompanyingText: string,
  token: vscode.CancellationToken,
): Promise<string> {
  const contentParts: OpenAIMessageContentPart[] = [{ type: "text", text: prompt }];
  if (accompanyingText.trim()) {
    contentParts.push({ type: "text", text: accompanyingText });
  }
  for (const image of images) {
    const base64 = Buffer.from(image.data).toString("base64");
    contentParts.push({
      type: "image_url",
      image_url: { url: `data:${image.mimeType};base64,${base64}` },
    });
  }

  const controller = new AbortController();
  const dispose = token.onCancellationRequested(() => controller.abort());
  let text = "";

  try {
    for await (const delta of client.chatCompletionStream(
      { model: visionModelId, messages: [{ role: "user", content: contentParts }], max_tokens: 2048 },
      controller.signal,
    )) {
      if (token.isCancellationRequested) {
        break;
      }
      if (delta.content) {
        text += delta.content;
      }
    }
  } catch {
    return "";
  } finally {
    dispose.dispose();
  }

  return text.trim();
}

function formatImageDescription(description: string): string {
  return `${IMAGE_DESCRIPTION_PREFIX}${description}${IMAGE_DESCRIPTION_SUFFIX}`;
}

function appendVisionText(
  nonImageParts: readonly vscode.LanguageModelInputPart[],
  visionText: string,
): string {
  const hasText = nonImageParts.some(
    (part) => part instanceof vscode.LanguageModelTextPart && part.value.trim().length > 0,
  );
  return hasText ? `\n\n${visionText}` : visionText;
}

function getImageParts(
  message: vscode.LanguageModelChatRequestMessage,
): vscode.LanguageModelDataPart[] {
  return message.content.filter(isImageDataPart);
}

function getNonImageParts(
  message: vscode.LanguageModelChatRequestMessage,
): vscode.LanguageModelInputPart[] {
  return message.content.filter(
    (part): part is vscode.LanguageModelInputPart => !isImageDataPart(part),
  );
}

function isImageDataPart(part: unknown): part is vscode.LanguageModelDataPart {
  return part instanceof vscode.LanguageModelDataPart && part.mimeType.startsWith("image/");
}

function collectTextFromParts(parts: readonly vscode.LanguageModelInputPart[]): string {
  const chunks: string[] = [];
  for (const part of parts) {
    if (part instanceof vscode.LanguageModelTextPart) {
      chunks.push(part.value);
    }
  }
  return chunks.join("");
}

function createResolvedMessage(
  message: vscode.LanguageModelChatRequestMessage,
  content: readonly vscode.LanguageModelInputPart[],
): vscode.LanguageModelChatRequestMessage {
  return { role: message.role, content, name: message.name } as vscode.LanguageModelChatRequestMessage;
}
