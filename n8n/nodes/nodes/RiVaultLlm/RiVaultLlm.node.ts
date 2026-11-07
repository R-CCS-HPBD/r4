import {
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	NodeOperationError,
} from 'n8n-workflow';

/**
 * RiVault LLM Node
 *
 * Provides access to RiVault LLM inference via Gateway.
 * Supports:
 * - Chat completions
 * - Text completions
 * - Tool/function calling
 */
export class RiVaultLlm implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'RiVault LLM',
		name: 'rivaultLlm',
		icon: 'file:rivault.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description: 'Interact with RiVault LLM inference',
		defaults: {
			name: 'RiVault LLM',
		},
		inputs: ['main'],
		outputs: ['main'],
		credentials: [
			{
				name: 'rivaultApi',
				required: true,
			},
		],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				options: [
					{
						name: 'Chat Completion',
						value: 'chatCompletion',
						description: 'Generate a chat completion',
						action: 'Generate a chat completion',
					},
					{
						name: 'Text Completion',
						value: 'textCompletion',
						description: 'Generate a text completion',
						action: 'Generate a text completion',
					},
					{
						name: 'Tool Call',
						value: 'toolCall',
						description: 'Execute a function/tool call',
						action: 'Execute a tool call',
					},
				],
				default: 'chatCompletion',
			},
			{
				displayName: 'Model',
				name: 'model',
				type: 'string',
				default: 'rivault-gpt-oss-static',
				description: 'The model to use for inference',
			},
			{
				displayName: 'Messages',
				name: 'messages',
				type: 'json',
				default: '[{"role": "user", "content": "Hello!"}]',
				displayOptions: {
					show: {
						operation: ['chatCompletion', 'toolCall'],
					},
				},
				description: 'The messages to send (JSON array)',
			},
			{
				displayName: 'Prompt',
				name: 'prompt',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['textCompletion'],
					},
				},
				description: 'The prompt for text completion',
			},
			{
				displayName: 'Tools',
				name: 'tools',
				type: 'json',
				default: '[]',
				displayOptions: {
					show: {
						operation: ['toolCall'],
					},
				},
				description: 'Tool definitions (JSON array)',
			},
			{
				displayName: 'Options',
				name: 'options',
				type: 'collection',
				placeholder: 'Add Option',
				default: {},
				options: [
					{
						displayName: 'Max Tokens',
						name: 'maxTokens',
						type: 'number',
						default: 1024,
						description: 'Maximum tokens to generate',
					},
					{
						displayName: 'Temperature',
						name: 'temperature',
						type: 'number',
						typeOptions: {
							minValue: 0,
							maxValue: 2,
							numberPrecision: 1,
						},
						default: 0.7,
						description: 'Sampling temperature (0-2)',
					},
					{
						displayName: 'Top P',
						name: 'topP',
						type: 'number',
						typeOptions: {
							minValue: 0,
							maxValue: 1,
							numberPrecision: 2,
						},
						default: 1,
						description: 'Nucleus sampling parameter',
					},
					{
						displayName: 'Stream',
						name: 'stream',
						type: 'boolean',
						default: false,
						description: 'Whether to stream responses',
					},
				],
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const returnData: INodeExecutionData[] = [];
		const credentials = await this.getCredentials('rivaultApi');

		const gatewayUrl = credentials.gatewayUrl as string;
		const operation = this.getNodeParameter('operation', 0) as string;
		const model = this.getNodeParameter('model', 0) as string;

		for (let i = 0; i < items.length; i++) {
			try {
				let response;
				const options = this.getNodeParameter('options', i, {}) as {
					maxTokens?: number;
					temperature?: number;
					topP?: number;
					stream?: boolean;
				};

				if (operation === 'chatCompletion' || operation === 'toolCall') {
					const messagesJson = this.getNodeParameter('messages', i) as string;
					const messages = JSON.parse(messagesJson);

					const requestBody: Record<string, unknown> = {
						model,
						messages,
						max_tokens: options.maxTokens || 1024,
						temperature: options.temperature || 0.7,
						top_p: options.topP || 1,
						stream: options.stream || false,
					};

					if (operation === 'toolCall') {
						const toolsJson = this.getNodeParameter('tools', i) as string;
						const tools = JSON.parse(toolsJson);
						requestBody.tools = tools;
						requestBody.tool_choice = 'auto';
					}

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${gatewayUrl}/gw/llm/v1/chat/completions`,
						body: requestBody,
						headers: {
							'Content-Type': 'application/json',
							Authorization: `Bearer ${credentials.litellmKey || credentials.apiKey}`,
						},
					});
				} else if (operation === 'textCompletion') {
					const prompt = this.getNodeParameter('prompt', i) as string;

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${gatewayUrl}/gw/llm/v1/completions`,
						body: {
							model,
							prompt,
							max_tokens: options.maxTokens || 1024,
							temperature: options.temperature || 0.7,
						},
						headers: {
							'Content-Type': 'application/json',
							Authorization: `Bearer ${credentials.litellmKey || credentials.apiKey}`,
						},
					});
				}

				returnData.push({
					json: response,
					pairedItem: { item: i },
				});
			} catch (error) {
				if (this.continueOnFail()) {
					returnData.push({
						json: { error: error.message },
						pairedItem: { item: i },
					});
					continue;
				}
				throw new NodeOperationError(this.getNode(), error, { itemIndex: i });
			}
		}

		return [returnData];
	}
}
