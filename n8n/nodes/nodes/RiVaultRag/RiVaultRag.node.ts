import {
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	NodeOperationError,
} from 'n8n-workflow';

/**
 * RiVault RAG Node
 *
 * Provides access to RiVault RAG (Retrieval-Augmented Generation) via UMRS.
 * Supports:
 * - Document ingestion
 * - Vector search
 * - RAG queries
 */
export class RiVaultRag implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'RiVault RAG',
		name: 'rivaultRag',
		icon: 'file:rivault.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description: 'Interact with RiVault RAG system',
		defaults: {
			name: 'RiVault RAG',
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
						name: 'Query',
						value: 'query',
						description: 'Query the RAG system',
						action: 'Query the RAG system',
					},
					{
						name: 'Ingest Document',
						value: 'ingest',
						description: 'Ingest a document into the vector store',
						action: 'Ingest a document',
					},
					{
						name: 'Vector Search',
						value: 'search',
						description: 'Perform vector similarity search',
						action: 'Perform vector search',
					},
					{
						name: 'List Collections',
						value: 'listCollections',
						description: 'List available document collections',
						action: 'List collections',
					},
				],
				default: 'query',
			},
			{
				displayName: 'Collection',
				name: 'collection',
				type: 'string',
				default: 'default',
				displayOptions: {
					show: {
						operation: ['query', 'ingest', 'search'],
					},
				},
				description: 'The document collection to use',
			},
			{
				displayName: 'Query',
				name: 'query',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['query', 'search'],
					},
				},
				description: 'The query text',
			},
			{
				displayName: 'Document Content',
				name: 'content',
				type: 'string',
				default: '',
				displayOptions: {
					show: {
						operation: ['ingest'],
					},
				},
				description: 'The document content to ingest',
			},
			{
				displayName: 'Document Metadata',
				name: 'metadata',
				type: 'json',
				default: '{}',
				displayOptions: {
					show: {
						operation: ['ingest'],
					},
				},
				description: 'Metadata for the document (JSON)',
			},
			{
				displayName: 'Options',
				name: 'options',
				type: 'collection',
				placeholder: 'Add Option',
				default: {},
				options: [
					{
						displayName: 'Top K',
						name: 'topK',
						type: 'number',
						default: 5,
						description: 'Number of results to retrieve',
					},
					{
						displayName: 'Score Threshold',
						name: 'scoreThreshold',
						type: 'number',
						typeOptions: {
							minValue: 0,
							maxValue: 1,
							numberPrecision: 2,
						},
						default: 0.7,
						description: 'Minimum similarity score threshold',
					},
					{
						displayName: 'Include Sources',
						name: 'includeSources',
						type: 'boolean',
						default: true,
						description: 'Whether to include source references',
					},
					{
						displayName: 'Generate Answer',
						name: 'generateAnswer',
						type: 'boolean',
						default: true,
						displayOptions: {
							show: {
								'/operation': ['query'],
							},
						},
						description: 'Whether to generate an LLM answer',
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

		for (let i = 0; i < items.length; i++) {
			try {
				let response;
				const options = this.getNodeParameter('options', i, {}) as {
					topK?: number;
					scoreThreshold?: number;
					includeSources?: boolean;
					generateAnswer?: boolean;
				};

				if (operation === 'query') {
					const collection = this.getNodeParameter('collection', i) as string;
					const query = this.getNodeParameter('query', i) as string;

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${gatewayUrl}/gw/rag/query`,
						body: {
							collection,
							query,
							top_k: options.topK || 5,
							score_threshold: options.scoreThreshold || 0.7,
							include_sources: options.includeSources !== false,
							generate_answer: options.generateAnswer !== false,
						},
						headers: {
							'Content-Type': 'application/json',
							Authorization: `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'ingest') {
					const collection = this.getNodeParameter('collection', i) as string;
					const content = this.getNodeParameter('content', i) as string;
					const metadataJson = this.getNodeParameter('metadata', i) as string;
					const metadata = JSON.parse(metadataJson);

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${gatewayUrl}/gw/rag/ingest`,
						body: {
							collection,
							content,
							metadata,
						},
						headers: {
							'Content-Type': 'application/json',
							Authorization: `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'search') {
					const collection = this.getNodeParameter('collection', i) as string;
					const query = this.getNodeParameter('query', i) as string;

					response = await this.helpers.httpRequest({
						method: 'POST',
						url: `${gatewayUrl}/gw/rag/search`,
						body: {
							collection,
							query,
							top_k: options.topK || 5,
							score_threshold: options.scoreThreshold || 0.7,
						},
						headers: {
							'Content-Type': 'application/json',
							Authorization: `Bearer ${credentials.apiKey}`,
						},
					});
				} else if (operation === 'listCollections') {
					response = await this.helpers.httpRequest({
						method: 'GET',
						url: `${gatewayUrl}/gw/rag/collections`,
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
