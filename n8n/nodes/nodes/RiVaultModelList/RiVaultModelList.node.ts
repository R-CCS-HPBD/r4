import {
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	NodeOperationError,
} from 'n8n-workflow';

export class RiVaultModelList implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'RiVault Models',
		name: 'riVaultModels',
		icon: 'file:rivault.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description: 'Manage RiVault model registry',
		defaults: {
			name: 'RiVault Models',
		},
		inputs: ['main'],
		outputs: ['main'],
		credentials: [
			{
				name: 'riVaultApi',
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
						name: 'Update Model',
						value: 'updateModel',
						description: 'Update model metadata',
						action: 'Update model metadata',
					},
					{
						name: 'Deprecate Model',
						value: 'deprecateModel',
						description: 'Mark model as deprecated',
						action: 'Deprecate model',
					},
				],
				default: 'listModels',
			},
			// List models
			{
				displayName: 'Scope',
				name: 'scope',
				type: 'options',
				options: [
					{ name: 'All', value: 'all' },
					{ name: 'System', value: 'system' },
					{ name: 'Project', value: 'project' },
					{ name: 'User', value: 'user' },
				],
				default: 'all',
				displayOptions: {
					show: {
						operation: ['listModels'],
					},
				},
				description: 'Filter models by scope',
			},
			{
				displayName: 'Project ID',
				name: 'projectId',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['listModels', 'registerModel'],
						scope: ['project'],
					},
				},
				description: 'Project ID for project-scoped models',
			},
			// Get/Update/Deprecate model
			{
				displayName: 'Model ID',
				name: 'modelId',
				type: 'string',
				default: '',
				required: true,
				displayOptions: {
					show: {
						operation: ['getModel', 'updateModel', 'deprecateModel'],
					},
				},
				description: 'The model ID',
			},
			// Register model
			{
				displayName: 'Model Name',
				name: 'modelName',
				type: 'string',
				default: '',
				required: true,
				displayOptions: {
					show: {
						operation: ['registerModel'],
					},
				},
				description: 'Name for the new model',
			},
			{
				displayName: 'Model Path',
				name: 'modelPath',
				type: 'string',
				default: '',
				required: true,
				displayOptions: {
					show: {
						operation: ['registerModel'],
					},
				},
				description: 'Path to model files on shared storage',
			},
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
				displayOptions: {
					show: {
						operation: ['registerModel'],
					},
				},
				description: 'Type of model',
			},
			{
				displayName: 'Description',
				name: 'description',
				type: 'string',
				typeOptions: {
					rows: 2,
				},
				default: '',
				displayOptions: {
					show: {
						operation: ['registerModel', 'updateModel'],
					},
				},
				description: 'Model description',
			},
			// Model metadata
			{
				displayName: 'Metadata',
				name: 'metadata',
				type: 'collection',
				placeholder: 'Add Metadata',
				default: {},
				displayOptions: {
					show: {
						operation: ['registerModel', 'updateModel'],
					},
				},
				options: [
					{
						displayName: 'Context Length',
						name: 'contextLength',
						type: 'number',
						default: 4096,
						description: 'Maximum context length',
					},
					{
						displayName: 'Parameters',
						name: 'parameters',
						type: 'string',
						default: '',
						description: 'Model parameter count (e.g., "7B")',
					},
					{
						displayName: 'Quantization',
						name: 'quantization',
						type: 'options',
						options: [
							{ name: 'None (FP16)', value: 'fp16' },
							{ name: 'Q4_K_M', value: 'Q4_K_M' },
							{ name: 'Q5_K_M', value: 'Q5_K_M' },
							{ name: 'Q8_0', value: 'Q8_0' },
							{ name: 'AWQ', value: 'awq' },
							{ name: 'GPTQ', value: 'gptq' },
						],
						default: 'fp16',
						description: 'Quantization format',
					},
					{
						displayName: 'License',
						name: 'license',
						type: 'string',
						default: '',
						description: 'Model license',
					},
					{
						displayName: 'Source',
						name: 'source',
						type: 'string',
						default: '',
						description: 'Model source (e.g., HuggingFace repo)',
					},
					{
						displayName: 'Tags',
						name: 'tags',
						type: 'string',
						default: '',
						description: 'Comma-separated tags',
					},
				],
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const returnData: INodeExecutionData[] = [];
		const credentials = await this.getCredentials('riVaultApi');
		const operation = this.getNodeParameter('operation', 0) as string;

		const baseUrl = credentials.baseUrl as string;

		for (let i = 0; i < items.length; i++) {
			try {
				let response;

				if (operation === 'listModels') {
					const scope = this.getNodeParameter('scope', i) as string;
					const projectId = this.getNodeParameter('projectId', i, '') as string;

					let url = `${baseUrl}/gw/models`;
					const params: string[] = [];
					if (scope !== 'all') params.push(`scope=${scope}`);
					if (projectId) params.push(`project_id=${projectId}`);
					if (params.length) url += `?${params.join('&')}`;

					response = await this.helpers.httpRequest({
						method: 'GET',
						url,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'getModel') {
					const modelId = this.getNodeParameter('modelId', i) as string;

					response = await this.helpers.httpRequest({
						method: 'GET',
						url: `${baseUrl}/gw/models/${modelId}`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'registerModel') {
					const modelName = this.getNodeParameter('modelName', i) as string;
					const modelPath = this.getNodeParameter('modelPath', i) as string;
					const modelType = this.getNodeParameter('modelType', i) as string;
					const scope = this.getNodeParameter('scope', i) as string;
					const projectId = this.getNodeParameter('projectId', i, '') as string;
					const description = this.getNodeParameter('description', i, '') as string;
					const metadata = this.getNodeParameter('metadata', i) as {
						contextLength?: number;
						parameters?: string;
						quantization?: string;
						license?: string;
						source?: string;
						tags?: string;
					};

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${baseUrl}/gw/models`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
							'Content-Type': 'application/json',
						},
						body: {
							name: modelName,
							path: modelPath,
							model_type: modelType,
							scope: scope === 'all' ? 'user' : scope,
							project_id: projectId || undefined,
							description: description || undefined,
							context_length: metadata.contextLength,
							parameters: metadata.parameters,
							quantization: metadata.quantization,
							license: metadata.license,
							source: metadata.source,
							tags: metadata.tags ? metadata.tags.split(',').map(t => t.trim()) : [],
						},
					});
				} else if (operation === 'updateModel') {
					const modelId = this.getNodeParameter('modelId', i) as string;
					const description = this.getNodeParameter('description', i, '') as string;
					const metadata = this.getNodeParameter('metadata', i) as {
						contextLength?: number;
						parameters?: string;
						quantization?: string;
						license?: string;
						source?: string;
						tags?: string;
					};

					response = await this.helpers.httpRequest({
						method: 'PATCH',
						url: `${baseUrl}/gw/models/${modelId}`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
							'Content-Type': 'application/json',
						},
						body: {
							description: description || undefined,
							context_length: metadata.contextLength,
							parameters: metadata.parameters,
							quantization: metadata.quantization,
							license: metadata.license,
							source: metadata.source,
							tags: metadata.tags ? metadata.tags.split(',').map(t => t.trim()) : undefined,
						},
					});
				} else if (operation === 'deprecateModel') {
					const modelId = this.getNodeParameter('modelId', i) as string;

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${baseUrl}/gw/models/${modelId}/deprecate`,
						headers: {
							'Authorization': `Bearer ${credentials.apiKey}`,
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
						json: { error: (error as Error).message },
						pairedItem: { item: i },
					});
					continue;
				}
				throw new NodeOperationError(this.getNode(), error as Error, { itemIndex: i });
			}
		}

		return [returnData];
	}
}
