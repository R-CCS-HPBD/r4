import {
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	NodeOperationError,
} from 'n8n-workflow';

/**
 * RiVault Model Node
 *
 * Provides access to RiVault Model Registry via UMRS.
 * Supports:
 * - List models
 * - Get model details
 * - Register models
 * - Trigger conversions
 */
export class RiVaultModel implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'RiVault Model',
		name: 'rivaultModel',
		icon: 'file:rivault.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description: 'Interact with RiVault Model Registry',
		defaults: {
			name: 'RiVault Model',
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
						name: 'List Models',
						value: 'listModels',
						description: 'List available models',
						action: 'List available models',
					},
					{
						name: 'Get Model',
						value: 'getModel',
						description: 'Get model details',
						action: 'Get model details',
					},
					{
						name: 'Register Model',
						value: 'registerModel',
						description: 'Register a new model',
						action: 'Register a new model',
					},
					{
						name: 'Convert Model',
						value: 'convertModel',
						description: 'Trigger model conversion',
						action: 'Trigger model conversion',
					},
					{
						name: 'Get Conversion Status',
						value: 'getConversionStatus',
						description: 'Get conversion job status',
						action: 'Get conversion status',
					},
				],
				default: 'listModels',
			},
			{
				displayName: 'Project ID',
				name: 'projectId',
				type: 'string',
				default: '',
				required: true,
				description: 'The project ID',
			},
			{
				displayName: 'Model ID',
				name: 'modelId',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['getModel', 'convertModel'],
					},
				},
				description: 'The model ID',
			},
			{
				displayName: 'Job ID',
				name: 'jobId',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['getConversionStatus'],
					},
				},
				description: 'The conversion job ID',
			},
			{
				displayName: 'Model Name',
				name: 'modelName',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['registerModel'],
					},
				},
				description: 'Name for the new model',
			},
			{
				displayName: 'Storage Path',
				name: 'storagePath',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['registerModel'],
					},
				},
				description: 'Path to model files',
			},
			{
				displayName: 'Conversion Type',
				name: 'conversionType',
				type: 'options',
				displayOptions: {
					show: {
						operation: ['convertModel'],
					},
				},
				options: [
					{
						name: 'FP32 to FP16',
						value: 'fp32_to_fp16',
					},
					{
						name: 'FP32 to BF16',
						value: 'fp32_to_bf16',
					},
					{
						name: 'AWQ (4-bit)',
						value: 'awq',
					},
					{
						name: 'GPTQ (4-bit)',
						value: 'gptq',
					},
					{
						name: 'GGUF Q4_K_M',
						value: 'gguf_q4_k_m',
					},
					{
						name: 'GGUF Q8_0',
						value: 'gguf_q8_0',
					},
				],
				default: 'awq',
				description: 'Type of model conversion',
			},
			{
				displayName: 'Options',
				name: 'options',
				type: 'collection',
				placeholder: 'Add Option',
				default: {},
				options: [
					{
						displayName: 'Model Type',
						name: 'modelType',
						type: 'options',
						options: [
							{ name: 'LLM', value: 'llm' },
							{ name: 'Embedding', value: 'embedding' },
							{ name: 'Vision', value: 'vision' },
							{ name: 'Audio', value: 'audio' },
						],
						default: 'llm',
						description: 'Type of model',
					},
					{
						displayName: 'Scope',
						name: 'scope',
						type: 'options',
						options: [
							{ name: 'User', value: 'user' },
							{ name: 'Project', value: 'project' },
							{ name: 'Global', value: 'global' },
						],
						default: 'user',
						description: 'Model visibility scope',
					},
					{
						displayName: 'Bits',
						name: 'bits',
						type: 'number',
						default: 4,
						description: 'Quantization bits (for AWQ/GPTQ)',
					},
					{
						displayName: 'Group Size',
						name: 'groupSize',
						type: 'number',
						default: 128,
						description: 'Quantization group size',
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
		const projectId = this.getNodeParameter('projectId', 0) as string;

		for (let i = 0; i < items.length; i++) {
			try {
				let response;
				const options = this.getNodeParameter('options', i, {}) as Record<string, unknown>;

				if (operation === 'listModels') {
					response = await this.helpers.httpRequest({
						method: 'GET',
						url: `${gatewayUrl}/api/v1/projects/${projectId}/models`,
						headers: {
							Authorization: `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'getModel') {
					const modelId = this.getNodeParameter('modelId', i) as string;

					response = await this.helpers.httpRequest({
						method: 'GET',
						url: `${gatewayUrl}/api/v1/projects/${projectId}/models/${modelId}`,
						headers: {
							Authorization: `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'registerModel') {
					const modelName = this.getNodeParameter('modelName', i) as string;
					const storagePath = this.getNodeParameter('storagePath', i) as string;

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${gatewayUrl}/api/v1/projects/${projectId}/models`,
						body: {
							name: modelName,
							storage_path: storagePath,
							model_type: options.modelType || 'llm',
							scope: options.scope || 'user',
						},
						headers: {
							'Content-Type': 'application/json',
							Authorization: `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'convertModel') {
					const modelId = this.getNodeParameter('modelId', i) as string;
					const conversionType = this.getNodeParameter('conversionType', i) as string;

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${gatewayUrl}/api/v1/projects/${projectId}/models/${modelId}/convert`,
						body: {
							conversion_type: conversionType,
							bits: options.bits || 4,
							group_size: options.groupSize || 128,
						},
						headers: {
							'Content-Type': 'application/json',
							Authorization: `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'getConversionStatus') {
					const jobId = this.getNodeParameter('jobId', i) as string;

					response = await this.helpers.httpRequest({
						method: 'GET',
						url: `${gatewayUrl}/api/v1/jobs/${jobId}`,
						headers: {
							Authorization: `Bearer ${credentials.apiKey}`,
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
